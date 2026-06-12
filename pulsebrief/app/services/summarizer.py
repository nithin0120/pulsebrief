"""Summarize articles using Groq or OpenAI, with an extractive fallback."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.config import settings
from app.services.news_fetcher import RawArticle

logger = logging.getLogger(__name__)


@dataclass
class ArticleSummary:
    tldr: str
    why_it_matters: str
    long_summary: str
    # importance 1-10 and best-fit category. None means "unknown" (e.g. AI unavailable).
    importance: int | None = None
    category: str | None = None


def _parse_importance(value: object) -> int | None:
    """Coerce the model's importance field to an int in 1-10, else None."""
    try:
        score = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return max(1, min(10, score))


def _match_category(value: object, categories: list[str]) -> str | None:
    """Map the model's category to a canonical topic name, or None if no clear fit."""
    raw = str(value or "").strip()
    if not raw or raw.lower() == "none":
        return None
    canon = {c.lower(): c for c in categories}
    return canon.get(raw.lower())


def _extractive_summary(article: RawArticle) -> ArticleSummary:
    desc = (article.description or "").strip()
    title = article.title.strip()
    sentences = re.split(r"(?<=[.!?])\s+", desc) if desc else []
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    if len(sentences) >= 2:
        tldr = " ".join(sentences[:2])
    elif sentences:
        tldr = f"{title}. {sentences[0]}"
    else:
        tldr = f"{title}. Details are limited from the available excerpt."

    why = (
        f"This story relates to {article.topic} and may affect ongoing developments in that area."
        if article.topic
        else "This story may be relevant to your tracked news topics."
    )
    long_parts = [title]
    if desc:
        long_parts.append(desc)
    if article.content:
        long_parts.append(article.content[:1500])
    long_summary = "\n\n".join(long_parts)

    return ArticleSummary(tldr=tldr, why_it_matters=why, long_summary=long_summary)


class Summarizer:
    """Uses Groq if GROQ_API_KEY is set, else OpenAI, else extractive fallback."""

    def __init__(self) -> None:
        self._client = None
        self._model = "gpt-4o-mini"
        self._provider = "none"

        if settings.groq_api_key:
            try:
                from openai import OpenAI

                # Groq exposes an OpenAI-compatible API.
                self._client = OpenAI(
                    api_key=settings.groq_api_key,
                    base_url="https://api.groq.com/openai/v1",
                )
                self._model = settings.groq_model
                self._provider = "groq"
                logger.info("Summarizer using Groq model '%s'", self._model)
            except Exception:
                logger.exception("Failed to initialize Groq client")
        elif settings.openai_api_key:
            try:
                from openai import OpenAI

                self._client = OpenAI(api_key=settings.openai_api_key)
                self._provider = "openai"
                logger.info("Summarizer using OpenAI model '%s'", self._model)
            except Exception:
                logger.exception("Failed to initialize OpenAI client")

    def summarize(
        self, article: RawArticle, categories: list[str] | None = None
    ) -> ArticleSummary:
        if not self._client:
            logger.info("Using extractive fallback for '%s'", article.title[:60])
            return _extractive_summary(article)

        categories = categories or []
        category_list = ", ".join(f'"{c}"' for c in categories)
        context = self._build_context(article)
        prompt = (
            "You are a strict news editor curating the most important stories of the day. "
            "Given the article below, respond with JSON only:\n"
            "{\n"
            '  "tldr": "Exactly 2 sentences summarizing the key facts.",\n'
            '  "why_it_matters": "1-2 sentences on significance and impact.",\n'
            '  "long_summary": "A 4-6 sentence detailed summary with key context.",\n'
            '  "importance": "Integer 1-10. 10 = major national/global development; '
            "7-9 = significant news; 4-6 = moderate; "
            '1-3 = trivial celebrity gossip, clickbait, or filler.",\n'
            f'  "category": "The SINGLE best fit from this exact list: [{category_list}]. '
            'If it does not clearly belong to any of these, return \\"None\\"."\n'
            "}\n\n"
            "Be honest about importance — most articles are not a 10. "
            "Only assign a category if the article is genuinely about that subject.\n\n"
            f"Source: {article.source}\n\n"
            f"{context}"
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "Return valid JSON only. No markdown fences."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=600,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            data = json.loads(content)
            return ArticleSummary(
                tldr=data.get("tldr", "").strip(),
                why_it_matters=data.get("why_it_matters", "").strip(),
                long_summary=data.get("long_summary", "").strip(),
                importance=_parse_importance(data.get("importance")),
                category=_match_category(data.get("category"), categories),
            )
        except Exception:
            logger.exception(
                "%s summarization failed for '%s'", self._provider, article.title[:60]
            )
            return _extractive_summary(article)

    @staticmethod
    def _build_context(article: RawArticle) -> str:
        parts = [f"Title: {article.title}"]
        if article.description:
            parts.append(f"Description: {article.description}")
        if article.content:
            parts.append(f"Content: {article.content[:2000]}")
        return "\n".join(parts)
