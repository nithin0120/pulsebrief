"""Format intelligence brief JSON for CLI and ntfy."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any

SECTION_EMOJI = {
    "world news": "🌎 Global",
    "us news": "🇺🇸 US",
    "ai": "💻 AI & Tech",
    "tech": "💻 AI & Tech",
    "cybersecurity": "🛡️ Cybersecurity",
    "finance": "💰 Markets",
    "markets": "💰 Markets",
    "startups": "🚀 Startups",
    "science": "🔬 Science",
}


def _section_label(topic: str) -> str:
    return SECTION_EMOJI.get(topic.lower(), topic)


def format_brief_json(brief: dict[str, Any] | None, when: datetime | None = None) -> str:
    if not brief or not brief.get("top_stories"):
        when = when or datetime.now()
        return f"Morning Brief — {when:%A, %B %d, %Y}\n\nNo stories met the bar today."

    date = brief.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    lines = [f"Morning Brief — {date}", ""]

    lines.append("🔥 Top Stories")
    for i, story in enumerate(brief.get("top_stories", []), 1):
        topic = story.get("topic", "")
        lines.append(f"{i}. [{topic}] {story.get('headline', '')}")
        if story.get("what_happened"):
            lines.append(f"What happened: {story['what_happened']}")
        if story.get("why_it_matters"):
            lines.append(f"Why it matters: {story['why_it_matters']}")
        if story.get("what_to_watch_next"):
            lines.append(f"Watch next: {story['what_to_watch_next']}")
        sources = story.get("sources") or []
        if sources:
            names = ", ".join(s.get("name", "") for s in sources[:4])
            lines.append(f"Sources: {names}")
        lines.append("")

    # Per-topic sections
    by_topic: OrderedDict[str, list[dict]] = OrderedDict()
    for story in brief.get("top_stories", []):
        by_topic.setdefault(story.get("topic", "Other"), []).append(story)

    for topic, stories in by_topic.items():
        if len(by_topic) <= 1:
            continue
        lines.append(_section_label(topic))
        for story in stories:
            lines.append(f"- {story.get('headline', '')}")
            if story.get("what_happened"):
                lines.append(f"  {story['what_happened'][:200]}")
        lines.append("")

    watchlist = brief.get("watchlist") or []
    if watchlist:
        lines.append("⚠️ Watchlist")
        for item in watchlist:
            reason = item.get("reason", "").replace("_", " ")
            lines.append(f"- {reason.title()}: {item.get('story', '')}")
        lines.append("")

    if brief.get("fallback"):
        lines.append("(Local fallback brief — Groq unavailable or over budget)")

    return "\n".join(lines).rstrip()


def format_ntfy_summary(brief: dict[str, Any], max_stories: int = 6) -> str:
    """Concise notification body (not a wall of text)."""
    lines: list[str] = []
    for i, story in enumerate(brief.get("top_stories", [])[:max_stories], 1):
        topic = story.get("topic", "")
        lines.append(f"{i}. [{topic}] {story.get('headline', '')}")
        if story.get("what_happened"):
            lines.append(f"What happened: {story['what_happened'][:180]}")
        if story.get("why_it_matters"):
            lines.append(f"Why it matters: {story['why_it_matters'][:140]}")
        if story.get("what_to_watch_next"):
            lines.append(f"Watch next: {story['what_to_watch_next'][:100]}")
        lines.append("")

    watchlist = brief.get("watchlist") or []
    if watchlist:
        lines.append("⚠️ Watchlist")
        for item in watchlist[:2]:
            lines.append(f"- {item.get('reason', '')}: {item.get('story', '')[:80]}")

    return "\n".join(lines).strip()


def format_story_more(story: dict[str, Any]) -> str:
    parts = [
        f"#{story.get('cluster_id')}: {story.get('headline', '')}",
        f"Topic: {story.get('topic', '')}",
        "",
    ]
    for key, label in [
        ("what_happened", "What happened"),
        ("background", "Background"),
        ("why_it_matters", "Why it matters"),
        ("what_is_uncertain", "What's uncertain"),
        ("what_to_watch_next", "Watch next"),
    ]:
        if story.get(key):
            parts.append(f"{label}: {story[key]}")
    comp = story.get("source_comparison") or {}
    if comp.get("agreement"):
        parts.append(f"Source agreement: {comp['agreement']}")
    if comp.get("differences"):
        parts.append(f"Source differences: {comp['differences']}")
    sources = story.get("sources") or []
    if sources:
        parts.append("")
        parts.append("Sources:")
        for s in sources:
            parts.append(f"- {s.get('name')}: {s.get('url')}")
    return "\n".join(parts)


def format_story_full(story: dict[str, Any], extracted: dict[str, str] | None = None) -> str:
    parts = [format_story_more(story)]
    for key, label in [
        ("who_is_affected", "Who is affected"),
        ("who_benefits", "Who benefits"),
        ("who_loses", "Who loses"),
    ]:
        if story.get(key):
            parts.append(f"{label}: {story[key]}")
    comp = story.get("source_comparison") or {}
    if comp.get("framing_or_bias"):
        parts.append(f"Framing/bias: {comp['framing_or_bias']}")
    if extracted:
        parts.append("")
        parts.append("--- Extracted article text ---")
        for url, text in list(extracted.items())[:2]:
            parts.append(f"\n{url}\n{text[:1500]}")
    return "\n".join(parts)


def format_story_sources(story: dict[str, Any]) -> str:
    lines = [f"Sources for #{story.get('cluster_id')}: {story.get('headline', '')}", ""]
    for s in story.get("sources") or []:
        lines.append(f"- {s.get('name')}: {s.get('url')}")
    return "\n".join(lines) if len(lines) > 2 else "No sources listed."
