"""Format intelligence brief JSON for CLI and ntfy."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings

SECTION_EMOJI = {
    "world news": "🌎 Global",
    "us news": "🇺🇸 US",
    "ai": "💻 AI",
    "tech": "💻 Tech",
    "cybersecurity": "🛡️ Cybersecurity",
    "finance": "💰 Finance",
    "markets": "💰 Markets",
    "startups": "🚀 Startups",
    "science": "🔬 Science",
}


def digest_greeting(when: datetime | None = None) -> str:
    """Time-of-day greeting for push titles, using TIMEZONE from .env."""
    tz = ZoneInfo(settings.timezone)
    now = (when or datetime.now(tz)).astimezone(tz)
    hour = now.hour
    if 5 <= hour < 12:
        return "Good Morning"
    if 12 <= hour < 17:
        return "Good Afternoon"
    if 17 <= hour < 22:
        return "Good Evening"
    return "News Update"


def digest_notification_title(brief: dict[str, Any] | None = None, when: datetime | None = None) -> str:
    """ntfy / brief page title, e.g. 'Good Afternoon - News Report 06/14/2026'."""
    tz = ZoneInfo(settings.timezone)
    now = (when or datetime.now(tz)).astimezone(tz)
    greeting = digest_greeting(when)
    date_str = now.strftime("%m/%d/%Y")
    return f"{greeting} - News Report {date_str}"


def format_ntfy_teaser() -> str:
    """Minimal ntfy body — full brief opens via Click URL."""
    return "Tap to read your full news report."


def _section_label(topic: str) -> str:
    return SECTION_EMOJI.get(topic.lower(), topic)


def format_brief_json(brief: dict[str, Any] | None, when: datetime | None = None) -> str:
    if not brief or not brief.get("top_stories"):
        when = when or datetime.now()
        greeting = digest_greeting(when)
        return f"{greeting} News Brief — {when:%A, %B %d, %Y}\n\nNo stories met the bar today."

    date = brief.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    greeting = digest_greeting(when)
    lines = [f"{greeting} News Brief — {date}", ""]

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


def _truncate_smart(text: str, max_chars: int, ellipsis: str = "…") -> str:
    """Trim to max_chars on a word or sentence boundary — never mid-word."""
    text = " ".join(text.split())
    if not text or len(text) <= max_chars:
        return text

    if max_chars <= len(ellipsis) + 10:
        return text[:max_chars]

    budget = max_chars - len(ellipsis)
    chunk = text[:budget]

    # Prefer a complete sentence when at least half the budget is used.
    for sep in (". ", "! ", "? "):
        idx = chunk.rfind(sep)
        if idx >= budget // 2:
            return text[: idx + 1].strip() + ellipsis

    if " " in chunk:
        chunk = chunk.rsplit(" ", 1)[0]

    return chunk.rstrip(".,;:- ") + ellipsis


def _story_summary(story: dict[str, Any], max_chars: int = 500) -> str:
    """Paragraph-length TLDR for push notifications."""
    parts: list[str] = []
    if story.get("what_happened"):
        parts.append(story["what_happened"].strip())
    if story.get("why_it_matters"):
        parts.append(story["why_it_matters"].strip())
    return _truncate_smart(" ".join(parts), max_chars)


def is_breaking_story(story: dict[str, Any]) -> bool:
    return story.get("confidence") == "high" and len(story.get("sources", [])) >= 2


def _ntfy_story_summary(story: dict[str, Any], max_chars: int) -> str:
    """Push summary — what_happened only to keep one notification under ntfy limits."""
    text = (story.get("what_happened") or story.get("background") or "").strip()
    return _truncate_smart(text, max_chars)


def _per_topic_summary_budget(story_count: int, max_body_chars: int, headline_max: int) -> int:
    """Split the ntfy body budget evenly across topic sections."""
    if story_count <= 0:
        return 280
    # Section header + headline line + blank lines per topic.
    overhead_per_topic = len("🌎 Global\n• \n\n") + headline_max + 20
    available = max_body_chars - (story_count * overhead_per_topic)
    return max(140, min(320, available // story_count))


def _build_ntfy_body(
    brief: dict[str, Any],
    *,
    max_per_section: int,
    headline_max: int,
    summary_max: int,
    include_watchlist: bool,
) -> str:
    stories = brief.get("top_stories") or []
    by_topic: OrderedDict[str, list[dict]] = OrderedDict()
    for story in stories:
        topic = story.get("topic") or "Other"
        bucket = by_topic.setdefault(topic, [])
        if len(bucket) < max_per_section:
            bucket.append(story)

    lines: list[str] = []
    for _topic, section_stories in by_topic.items():
        lines.append(_section_label(_topic))
        for story in section_stories:
            headline = _truncate_smart((story.get("headline") or "").strip(), headline_max, ellipsis="")
            prefix = "BREAKING — " if is_breaking_story(story) else "• "
            lines.append(f"{prefix}{headline}")
            summary = _ntfy_story_summary(story, summary_max)
            if summary:
                lines.append(summary)
            lines.append("")
        lines.append("")

    if include_watchlist:
        watchlist = brief.get("watchlist") or []
        if watchlist:
            lines.append("⚠️ Watchlist")
            for item in watchlist[:2]:
                lines.append(f"• {_truncate_smart(item.get('story', ''), 80)}")
            lines.append("")

    return "\n".join(lines).strip()


def format_ntfy_by_section(
    brief: dict[str, Any],
    max_per_section: int = 3,
    headline_max: int = 100,
    summary_max: int = 280,
    max_body_chars: int = 3400,
) -> str:
    """Single ntfy body: all topics, auto-shrinks to fit one push notification."""
    if not brief.get("top_stories"):
        return "No stories met the bar today."

    story_count = len({s.get("topic") for s in brief["top_stories"]})
    budget = min(summary_max, _per_topic_summary_budget(story_count, max_body_chars, headline_max))

    for sm in (budget, int(budget * 0.85), int(budget * 0.7), 160, 140):
        if sm < 100:
            continue
        body = _build_ntfy_body(
            brief,
            max_per_section=max_per_section,
            headline_max=headline_max,
            summary_max=sm,
            include_watchlist=False,
        )
        if len(body) <= max_body_chars:
            return body

    return _build_ntfy_body(
        brief,
        max_per_section=max_per_section,
        headline_max=headline_max,
        summary_max=120,
        include_watchlist=False,
    )


def format_ntfy_summary(brief: dict[str, Any], max_stories: int = 6) -> str:
    """Concise flat summary (legacy); prefer format_ntfy_by_section for push delivery."""
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
