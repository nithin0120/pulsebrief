"""Stage 9: ONE batched Groq call for the final intelligence brief."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from app.config import settings
from app.services.pipeline.article import ClusterContext
from app.services.pipeline.fallback import build_fallback_brief
from app.services.summarizer import _extract_json

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class BriefGenerator:
    """Generates the full digest in a single Groq request."""

    def __init__(self) -> None:
        self._client = None
        self._model = settings.groq_model
        if settings.groq_api_key:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=settings.groq_api_key,
                    base_url="https://api.groq.com/openai/v1",
                    max_retries=1,  # don't hammer rate limits
                )
            except Exception:
                logger.exception("Failed to init Groq client")

    @property
    def available(self) -> bool:
        return self._client is not None

    def generate_digest(
        self,
        contexts: list[ClusterContext],
        *,
        clusters_for_fallback,
        max_tokens: int = 6000,
    ) -> tuple[dict[str, Any], int, int]:
        """Return (brief_json, est_input_tokens, est_output_tokens)."""
        if not contexts:
            brief = build_fallback_brief([], clusters_for_fallback)
            return brief, 0, 0

        if not self._client:
            return build_fallback_brief(contexts, clusters_for_fallback), 0, 0

        payload = [c.to_dict() for c in contexts]

        def _trim(pl: list, aggressive: bool = False) -> None:
            for ctx in pl:
                desc_len = 50 if aggressive else 100
                ctx["descriptions"] = [d[:desc_len] for d in ctx.get("descriptions", [])[:2]]
                ctx["extracted_key_sentences"] = ctx.get("extracted_key_sentences", [])[
                    : 1 if aggressive else 2
                ]
                ctx["titles"] = ctx.get("titles", [])[:2]
                ctx["urls"] = ctx.get("urls", [])[:2]
                ctx["sources"] = ctx.get("sources", [])[:2]

        _trim(payload)
        while _estimate_tokens(json.dumps(payload)) > 2800 and len(payload) > 3:
            _trim(payload, aggressive=True)
            if _estimate_tokens(json.dumps(payload)) > 2800:
                payload = payload[: len(payload) - 1]

        def _call(pl: list) -> tuple[dict | None, str, int, int]:
            pr = (
                "You are a senior intelligence analyst. Given story cluster contexts "
                "(already deduplicated), return ONE JSON object:\n"
                '{"date":"YYYY-MM-DD","brief_title":"Morning Brief","top_stories":[{'
                '"cluster_id":1,"headline":"...","topic":"...","what_happened":"...",'
                '"background":"...","why_it_matters":"...","who_is_affected":"...",'
                '"who_benefits":"...","who_loses":"...","what_is_uncertain":"...",'
                '"what_to_watch_next":"...",'
                '"source_comparison":{"agreement":"...","differences":"...","framing_or_bias":"..."},'
                '"confidence":"low|medium|high","sources":[{"name":"...","url":"..."}]}],'
                '"watchlist":[{"story":"...","reason":"likely to develop|underreported|conflicting reports"}]}\n'
                "You MUST return exactly one top_stories entry for every cluster_id in CLUSTERS.\n"
                "Write what_happened as a clear 3-4 sentence paragraph (roughly 60-90 words).\n"
                "Write why_it_matters as one sentence explaining significance.\n"
                "Set confidence=high only for major breaking news confirmed by multiple sources.\n"
                "Be factual; do not invent details beyond the provided context.\n\n"
                f"CLUSTERS:\n{json.dumps(pl, ensure_ascii=False)}"
            )
            est_in = _estimate_tokens(pr)
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": pr},
                ],
                temperature=0.2,
                max_tokens=min(2400, max_tokens),
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or ""
            return _extract_json(content), content, est_in, _estimate_tokens(content)

        try:
            data, content, est_in, est_out = _call(payload)
            if not data or not data.get("top_stories"):
                # Retry with half the clusters if first attempt was empty
                if len(payload) > 2:
                    data, content, est_in, est_out = _call(payload[: max(2, len(payload) // 2)])
            if not data or not data.get("top_stories"):
                logger.warning("Groq returned unparseable brief; using fallback")
                return build_fallback_brief(contexts, clusters_for_fallback), est_in, est_out
            data.setdefault("date", datetime.utcnow().strftime("%Y-%m-%d"))
            data.setdefault("brief_title", "Morning Brief")
            data["fallback"] = False
            return data, est_in, est_out
        except Exception as exc:
            err = str(exc)
            if "413" in err or "too large" in err.lower() or "tokens" in err.lower():
                logger.warning("Groq payload too large; retrying with trimmed clusters")
                try:
                    smaller = list(payload)
                    _trim(smaller, aggressive=True)
                    while len(smaller) > 3 and _estimate_tokens(json.dumps(smaller)) > 2400:
                        smaller = smaller[: len(smaller) - 1]
                    data, content, est_in, est_out = _call(smaller)
                    if data and data.get("top_stories"):
                        data["fallback"] = False
                        return data, est_in, est_out
                except Exception:
                    pass
            logger.warning("Batched Groq brief failed: %s", exc)
            return build_fallback_brief(contexts, clusters_for_fallback), 0, 0

    def generate_explain(self, cluster_context: dict[str, Any]) -> str | None:
        if not self._client:
            return None
        model = settings.groq_deep_model
        prompt = (
            "Analyze the news story below. Respond with JSON only. "
            "Every value MUST be a plain string (no nested objects).\n"
            '{"background":"...","what_happened_today":"...","why_it_matters":"...",'
            '"who_benefits":"...","who_is_hurt":"...","what_to_watch_next":"..."}\n\n'
            f"Context:\n{json.dumps(cluster_context, ensure_ascii=False)}"
        )
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=900,
                response_format={"type": "json_object"},
            )
            data = _extract_json(response.choices[0].message.content or "")
            if not data:
                return None
            lines = ["Deep explainer:"]
            for key, label in [
                ("background", "Background"),
                ("what_happened_today", "What happened today"),
                ("why_it_matters", "Why it matters"),
                ("who_benefits", "Who benefits"),
                ("who_is_hurt", "Who is hurt"),
                ("what_to_watch_next", "What to watch next"),
            ]:
                if data.get(key):
                    lines.append(f"{label}: {data[key]}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("explain() failed: %s", exc)
            return None

    def generate_compare(self, cluster_context: dict[str, Any]) -> str | None:
        if not self._client:
            return None
        prompt = (
            "Compare how different sources frame this story. Return JSON:\n"
            '{"agreement":"...","differences":"...","framing_by_source":[{"source":"...","angle":"..."}]}\n\n'
            f"Context:\n{json.dumps(cluster_context, ensure_ascii=False)}"
        )
        try:
            response = self._client.chat.completions.create(
                model=settings.groq_deep_model,
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=700,
                response_format={"type": "json_object"},
            )
            data = _extract_json(response.choices[0].message.content or "")
            if not data:
                return None
            lines = ["Source comparison:"]
            if data.get("agreement"):
                lines.append(f"Agreement: {data['agreement']}")
            if data.get("differences"):
                lines.append(f"Differences: {data['differences']}")
            for item in data.get("framing_by_source", []):
                lines.append(f"- {item.get('source')}: {item.get('angle')}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("compare() failed: %s", exc)
            return None
