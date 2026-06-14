"""Build the digest from local extractive summaries (one headline per topic)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.pipeline.article import ClusterContext, StoryClusterData
from app.services.pipeline.fallback import _why_heuristic


def _extractive_paragraph(ctx: ClusterContext) -> str:
    """2–4 sentences from extracted article text or descriptions."""
    sents = [s.strip() for s in ctx.extracted_key_sentences if s and s.strip()]
    if len(sents) >= 2:
        return " ".join(sents[:4])
    if sents:
        return sents[0]
    for desc in ctx.descriptions:
        if desc and len(desc.strip()) > 40:
            return desc.strip()
    return ctx.cluster_title


def _story_from_context(
    cluster: StoryClusterData,
    ctx: ClusterContext,
    cluster_id: int,
) -> dict[str, Any]:
    sources = [
        {"name": name, "url": url}
        for name, url in zip(ctx.sources, ctx.urls)
        if name and url
    ]
    if not sources and cluster.representative:
        sources = [{"name": cluster.representative.source, "url": cluster.representative.url}]

    return {
        "cluster_id": cluster_id,
        "headline": ctx.cluster_title,
        "topic": ctx.topic,
        "what_happened": _extractive_paragraph(ctx),
        "background": ctx.descriptions[0] if ctx.descriptions else "",
        "why_it_matters": _why_heuristic(cluster),
        "who_is_affected": "",
        "who_benefits": "",
        "who_loses": "",
        "what_is_uncertain": "",
        "what_to_watch_next": "Follow updates from primary sources.",
        "source_comparison": {
            "agreement": f"Reported by {ctx.source_count} source(s).",
            "differences": "",
            "framing_or_bias": "",
        },
        "confidence": "high" if ctx.source_count >= 2 and cluster.is_breaking else (
            "medium" if ctx.source_count >= 2 else "low"
        ),
        "sources": sources,
        "ai_enriched": False,
    }


def build_local_brief(
    per_topic: list[StoryClusterData],
    contexts: list[ClusterContext],
) -> dict[str, Any]:
    """One top story per topic with extractive summaries — no LLM required."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    ctx_map = {c.cluster_id: c for c in contexts}
    top_stories: list[dict[str, Any]] = []

    for i, cluster in enumerate(per_topic, 1):
        ctx = ctx_map.get(i)
        if not ctx:
            continue
        top_stories.append(_story_from_context(cluster, ctx, i))

    watchlist: list[dict[str, str]] = []
    multi = [c for c in per_topic if c.source_count >= 2]
    if multi:
        watchlist.append({"story": multi[0].cluster_title, "reason": "likely to develop"})
    single = [c for c in per_topic if c.source_count == 1]
    if single:
        watchlist.append({"story": single[0].cluster_title, "reason": "underreported"})

    return {
        "date": today,
        "brief_title": "News Brief",
        "top_stories": top_stories,
        "watchlist": watchlist[:2],
        "fallback": False,
        "local": True,
        "topics_covered": len(top_stories),
    }


def merge_groq_polish(
    local: dict[str, Any],
    groq: dict[str, Any] | None,
    topics_to_enrich: set[str],
) -> dict[str, Any]:
    """Overlay AI paragraphs onto local stories for selected topics."""
    if not groq or groq.get("fallback") or not groq.get("top_stories"):
        return local

    groq_by_topic = {s["topic"]: s for s in groq["top_stories"]}
    enriched = 0
    for story in local.get("top_stories", []):
        topic = story.get("topic", "")
        if topic not in topics_to_enrich:
            continue
        ai = groq_by_topic.get(topic)
        if not ai:
            continue
        for field in (
            "what_happened",
            "why_it_matters",
            "background",
            "what_to_watch_next",
            "source_comparison",
            "confidence",
        ):
            value = ai.get(field)
            if value:
                story[field] = value
        story["ai_enriched"] = True
        enriched += 1

    if enriched:
        local["groq_polished"] = enriched
    return local
