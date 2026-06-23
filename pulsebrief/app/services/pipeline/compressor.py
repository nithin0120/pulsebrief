"""Stage 8: extractive compression before Groq."""

from __future__ import annotations

import re
from typing import Any

from app.config import load_config
from app.services.pipeline.article import ClusterContext, StoryClusterData

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _key_sentences(text: str, limit: int) -> list[str]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if len(s.strip()) > 40]
    if not sentences:
        return []
    # Simple lead + longest impactful sentences heuristic (TextRank-lite).
    scored: list[tuple[float, str]] = []
    for i, s in enumerate(sentences[:20]):
        score = 0.0
        if i < 2:
            score += 2.0
        score += min(2.0, len(s) / 200)
        impact = sum(
            1
            for cue in ("could", "will", "expected", "first", "record", "billion", "warn", "ban")
            if cue in s.lower()
        )
        score += impact * 0.5
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [s for _, s in scored[:limit]]
    return picked


class ContextCompressor:
    def __init__(self, config: dict | None = None) -> None:
        cfg = (config or load_config()).get("compression", {})
        self.key_sentences = int(cfg.get("key_sentences_per_cluster", 5))
        self.max_tokens = int(cfg.get("max_tokens_per_cluster", 700))

    def build_contexts(
        self,
        clusters: list[StoryClusterData],
        extracted: dict[str, str],
        max_clusters: int | None = None,
    ) -> list[ClusterContext]:
        clusters = clusters[: max_clusters or len(clusters)]
        contexts: list[ClusterContext] = []
        for i, cluster in enumerate(clusters, 1):
            rep = cluster.representative
            titles = [a.title for a in cluster.articles[:4]]
            descriptions = [(a.description or "")[:400] for a in cluster.articles[:4]]
            urls = [a.url for a in cluster.articles[:4]]
            sources = cluster.source_names[:4]
            dates = [
                a.published_at.strftime("%Y-%m-%d") if a.published_at else "unknown"
                for a in cluster.articles[:4]
            ]

            key_sents: list[str] = []
            for article in cluster.articles[:3]:
                body = extracted.get(article.url) or article.content or article.description or ""
                key_sents.extend(_key_sentences(body, self.key_sentences))
            key_sents = list(dict.fromkeys(key_sents))[: self.key_sentences]

            full_text_available = bool(rep and rep.url in extracted)
            diversity = (
                "high" if cluster.source_count >= 3 else "medium" if cluster.source_count == 2 else "low"
            )

            ctx = ClusterContext(
                cluster_id=i,
                topic=cluster.topic,
                cluster_title=cluster.cluster_title,
                sources=sources,
                urls=urls,
                published_dates=dates,
                titles=titles,
                descriptions=descriptions,
                extracted_key_sentences=key_sents,
                source_count=cluster.source_count,
                confidence_signals={
                    "multi_source": cluster.source_count >= 2,
                    "source_diversity": diversity,
                    "full_text_available": full_text_available,
                    "is_opinion": cluster.is_opinion,
                },
            )
            # Trim if over token budget
            blob = str(ctx.to_dict())
            if _estimate_tokens(blob) > self.max_tokens:
                ctx.descriptions = [d[:120] for d in ctx.descriptions]
                ctx.extracted_key_sentences = ctx.extracted_key_sentences[:3]
            contexts.append(ctx)
        return contexts
