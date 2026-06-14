"""Stage 14: non-LLM fallback digest when Groq is unavailable or over budget."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.pipeline.article import ClusterContext, StoryClusterData


def _why_heuristic(cluster: StoryClusterData) -> str:
    topic = cluster.topic.lower()
    if "cyber" in topic:
        return "Security developments can affect systems, data, and trust at scale."
    if "market" in topic or "finance" in topic:
        return "Markets and policy moves can shift portfolios, jobs, and consumer costs."
    if "world" in topic or "us news" in topic:
        return "Geopolitical and policy shifts can reshape alliances, trade, and daily life."
    if "ai" in topic or "tech" in topic:
        return "Technology shifts can change products, competition, and regulation quickly."
    return f"A significant {cluster.topic} development worth tracking."


def build_fallback_brief(
    contexts: list[ClusterContext],
    clusters: list[StoryClusterData],
) -> dict[str, Any]:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    top_stories = []
    cluster_map = {i + 1: c for i, c in enumerate(clusters)}

    for ctx in contexts:
        cluster = cluster_map.get(ctx.cluster_id)
        desc = ctx.descriptions[0] if ctx.descriptions else ctx.cluster_title
        what = ctx.extracted_key_sentences[0] if ctx.extracted_key_sentences else desc
        top_stories.append(
            {
                "cluster_id": ctx.cluster_id,
                "headline": ctx.cluster_title,
                "topic": ctx.topic,
                "what_happened": what,
                "background": desc,
                "why_it_matters": _why_heuristic(cluster) if cluster else "",
                "who_is_affected": "Readers following this topic.",
                "who_benefits": "Unclear without deeper analysis.",
                "who_loses": "Unclear without deeper analysis.",
                "what_is_uncertain": "Details may evolve as more reporting emerges.",
                "what_to_watch_next": "Follow updates from primary sources.",
                "source_comparison": {
                    "agreement": f"Reported by {ctx.source_count} source(s).",
                    "differences": "Compare framing across listed outlets.",
                    "framing_or_bias": "Not assessed (local fallback).",
                },
                "confidence": "medium" if ctx.source_count >= 2 else "low",
                "sources": [{"name": s, "url": u} for s, u in zip(ctx.sources, ctx.urls)],
            }
        )

    watchlist = []
    if clusters:
        watchlist.append(
            {
                "story": clusters[0].cluster_title,
                "reason": "likely to develop",
            }
        )
    under = [c for c in clusters if c.source_count == 1]
    if under:
        watchlist.append({"story": under[0].cluster_title, "reason": "underreported"})
    multi = [c for c in clusters if c.source_count >= 2]
    if len(multi) >= 2:
        watchlist.append(
            {"story": multi[1].cluster_title, "reason": "conflicting reports"}
        )

    return {
        "date": today,
        "brief_title": "Morning Brief",
        "top_stories": top_stories,
        "watchlist": watchlist[:3],
        "fallback": True,
    }
