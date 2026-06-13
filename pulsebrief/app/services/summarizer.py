"""Summarize articles using Groq or OpenAI, with an extractive fallback."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.config import settings
from app.services.news_fetcher import RawArticle

logger = logging.getLogger(__name__)


@dataclass
class ArticleSummary:
    tldr: str
    why_it_matters: str
    long_summary: str
    bias_or_angle: str | None = None
    key_entities: list[str] = field(default_factory=list)
    follow_up_question: str | None = None
    background: str | None = None
    what_changed_today: str | None = None
    what_to_watch_next: str | None = None
    # importance 1-10 and best-fit category. None means "unknown" (AI unavailable).
    importance: int | None = None
    category: str | None = None
    # True when produced by the extractive fallback (no AI insight).
    is_fallback: bool = False


@dataclass
class TriageResult:
    """Cheap importance/category assessment used to pick which few articles
    are worth a full (token-expensive) rich summary."""

    importance: int | None = None
    category: str | None = None


@dataclass
class ArticleExplain:
    background: str
    what_happened_today: str
    why_it_matters: str
    who_benefits: str
    who_is_hurt: str
    what_to_watch_next: str


def _parse_importance(value: object) -> int | None:
    try:
        score = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return max(1, min(10, score))


def _match_category(value: object, categories: list[str]) -> str | None:
    raw = str(value or "").strip()
    if not raw or raw.lower() == "none":
        return None
    canon = {c.lower(): c for c in categories}
    return canon.get(raw.lower())


def _as_str(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _as_list(value: object, limit: int = 6) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()][:limit]
    if isinstance(value, str) and value.strip():
        return [p.strip() for p in re.split(r"[,;]", value) if p.strip()][:limit]
    return []


def _extract_json(content: str) -> dict | None:
    """Tolerant JSON extraction: strips fences, then grabs the first {...} block."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _fallback_why(article: RawArticle, sentences: list[str]) -> str:
    impact_cues = (
        "could", "would", "may", "expected", "impact", "after", "because",
        "raising", "amid", "leading", "threat", "risk", "first", "record",
        "billion", "million", "ban", "warn", "rule", "court", "deal",
    )
    candidates = [s for s in sentences if any(cue in s.lower() for cue in impact_cues)]
    if candidates:
        return max(candidates, key=len)
    if len(sentences) >= 2:
        return sentences[-1]
    return f"A developing {article.topic} story to keep an eye on."


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

    why = _fallback_why(article, sentences)
    long_parts = [title]
    if desc:
        long_parts.append(desc)
    if article.content:
        long_parts.append(article.content[:1500])

    return ArticleSummary(
        tldr=tldr,
        why_it_matters=why,
        long_summary="\n\n".join(long_parts),
        what_changed_today=sentences[0] if sentences else None,
        is_fallback=True,
    )


class Summarizer:
    """Uses Groq if GROQ_API_KEY is set, else OpenAI, else extractive fallback."""

    def __init__(self) -> None:
        self._client = None
        self._model = "gpt-4o-mini"
        self._provider = "none"

        if settings.groq_api_key:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=settings.groq_api_key,
                    base_url="https://api.groq.com/openai/v1",
                    max_retries=3,  # built-in exponential backoff on 429/5xx
                )
                self._model = settings.groq_model
                self._provider = "groq"
                logger.info("Summarizer using Groq model '%s'", self._model)
            except Exception:
                logger.exception("Failed to initialize Groq client")
        elif settings.openai_api_key:
            try:
                from openai import OpenAI

                self._client = OpenAI(api_key=settings.openai_api_key, max_retries=3)
                self._provider = "openai"
                logger.info("Summarizer using OpenAI model '%s'", self._model)
            except Exception:
                logger.exception("Failed to initialize OpenAI client")

    @property
    def provider(self) -> str:
        return self._provider

    def _chat(self, prompt: str, max_tokens: int) -> str | None:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": "Return valid JSON only. No markdown fences."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    def triage(
        self, articles: list[RawArticle], categories: list[str], batch_size: int = 8
    ) -> list[TriageResult]:
        """Score importance + category for many articles cheaply, in batches.

        This lets the pipeline spend full rich-summary tokens only on finalists
        instead of every candidate — critical on Groq's free per-minute limits.
        """
        if not self._client or not articles:
            return [TriageResult() for _ in articles]

        results: list[TriageResult] = []
        for start in range(0, len(articles), batch_size):
            batch = articles[start : start + batch_size]
            results.extend(self._triage_batch(batch, categories))
        return results

    def _triage_batch(
        self, batch: list[RawArticle], categories: list[str]
    ) -> list[TriageResult]:
        category_list = ", ".join(f'"{c}"' for c in categories)
        lines = []
        for i, art in enumerate(batch):
            desc = (art.description or "")[:160]
            lines.append(f"[{i}] ({art.source}) {art.title} — {desc}")
        prompt = (
            "You are a strict news editor. For EACH numbered article, rate its "
            "importance 1-10 (10 = major national/global development; 1-3 = trivial "
            "gossip/clickbait/filler) and choose the single best category from this "
            f"exact list: [{category_list}], or \"None\" if nothing fits.\n"
            'Respond with JSON only: {"results": [{"i": 0, "importance": 7, '
            '"category": "..."}, ...]} with one entry per article.\n\n'
            + "\n".join(lines)
        )
        try:
            content = self._chat(prompt, max_tokens=60 + 30 * len(batch)) or ""
        except Exception as exc:
            logger.warning("Triage batch failed: %s", exc)
            return [TriageResult() for _ in batch]

        data = _extract_json(content)
        out = [TriageResult() for _ in batch]
        if not data:
            return out
        for entry in data.get("results", []) if isinstance(data, dict) else []:
            try:
                idx = int(entry.get("i"))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                out[idx] = TriageResult(
                    importance=_parse_importance(entry.get("importance")),
                    category=_match_category(entry.get("category"), categories),
                )
        return out

    def summarize(
        self, article: RawArticle, categories: list[str] | None = None
    ) -> ArticleSummary:
        if not self._client:
            return _extractive_summary(article)

        categories = categories or []
        category_list = ", ".join(f'"{c}"' for c in categories)
        prompt = (
            "You are a strict news editor and analyst. Read the article and respond with "
            "JSON only using exactly these keys:\n"
            "{\n"
            '  "tldr": "Exactly 2 sentences with the key facts.",\n'
            '  "why_it_matters": "1-2 sentences on significance and impact.",\n'
            '  "bias_or_angle": "1 sentence on the framing, slant, or angle of this coverage.",\n'
            '  "key_entities": ["3-6 people, organizations, places, or products central to the story"],\n'
            '  "follow_up_question": "One sharp question a curious reader would ask next.",\n'
            '  "background": "2-3 sentences of context a newcomer needs.",\n'
            '  "what_changed_today": "1-2 sentences on what is actually new right now.",\n'
            '  "what_to_watch_next": "1-2 sentences on what to watch going forward.",\n'
            '  "long_summary": "A 4-6 sentence detailed summary.",\n'
            '  "importance": "Integer 1-10. 10 = major national/global development; 7-9 = '
            'significant; 4-6 = moderate; 1-3 = trivial gossip, clickbait, or filler.",\n'
            f'  "category": "Single best fit from this exact list: [{category_list}]. '
            'If none clearly fit, return \\"None\\"."\n'
            "}\n\n"
            "Be honest about importance — most articles are not a 10. "
            "Only assign a category if the article genuinely belongs to it.\n\n"
            f"Source: {article.source}\n\n"
            f"{self._build_context(article)}"
        )

        try:
            content = self._chat(prompt, max_tokens=900) or ""
        except Exception as exc:
            logger.warning("%s summarization error for '%s': %s", self._provider, article.title[:60], exc)
            fallback = _extractive_summary(article)
            fallback.is_fallback = True
            return fallback

        data = _extract_json(content)
        if not data or not _as_str(data.get("tldr")):
            logger.warning("Unparseable summary JSON for '%s'; using fallback", article.title[:60])
            fallback = _extractive_summary(article)
            return fallback

        return ArticleSummary(
            tldr=_as_str(data.get("tldr")),
            why_it_matters=_as_str(data.get("why_it_matters")),
            long_summary=_as_str(data.get("long_summary")) or _as_str(data.get("tldr")),
            bias_or_angle=_as_str(data.get("bias_or_angle")) or None,
            key_entities=_as_list(data.get("key_entities")),
            follow_up_question=_as_str(data.get("follow_up_question")) or None,
            background=_as_str(data.get("background")) or None,
            what_changed_today=_as_str(data.get("what_changed_today")) or None,
            what_to_watch_next=_as_str(data.get("what_to_watch_next")) or None,
            importance=_parse_importance(data.get("importance")),
            category=_match_category(data.get("category"), categories),
        )

    def explain(self, article) -> ArticleExplain | None:
        """On-demand deep dive over an Article/RawArticle. None if no AI configured."""
        if not self._client:
            return None
        prompt = (
            "Analyze the news story below. Respond with JSON only, keys exactly:\n"
            "{\n"
            '  "background": "2-3 sentences of context.",\n'
            '  "what_happened_today": "What is newly happening.",\n'
            '  "why_it_matters": "Why this is significant.",\n'
            '  "who_benefits": "Who gains and how.",\n'
            '  "who_is_hurt": "Who loses or is at risk and how.",\n'
            '  "what_to_watch_next": "What to watch going forward."\n'
            "}\n\n"
            f"Title: {article.title}\n"
            f"Source: {article.source}\n"
            f"Summary: {getattr(article, 'long_summary', None) or getattr(article, 'tldr', '') or ''}\n"
            f"Excerpt: {getattr(article, 'description', '') or ''}"
        )
        try:
            content = self._chat(prompt, max_tokens=700) or ""
        except Exception as exc:
            logger.warning("explain() failed: %s", exc)
            return None
        data = _extract_json(content)
        if not data:
            return None
        return ArticleExplain(
            background=_as_str(data.get("background")),
            what_happened_today=_as_str(data.get("what_happened_today")),
            why_it_matters=_as_str(data.get("why_it_matters")),
            who_benefits=_as_str(data.get("who_benefits")),
            who_is_hurt=_as_str(data.get("who_is_hurt")),
            what_to_watch_next=_as_str(data.get("what_to_watch_next")),
        )

    @staticmethod
    def _build_context(article: RawArticle) -> str:
        parts = [f"Title: {article.title}"]
        if article.description:
            parts.append(f"Description: {article.description}")
        if article.content:
            parts.append(f"Content: {article.content[:2000]}")
        return "\n".join(parts)
