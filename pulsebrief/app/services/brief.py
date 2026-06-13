"""Format a daily intelligence brief from stored articles and clusters."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime

from app.models import Article, StoryCluster


def _opinion_tag(article: Article) -> str:
    return " [Opinion/Analysis]" if article.is_opinion else ""


def format_brief(
    articles: list[Article],
    clusters: list[StoryCluster] | None = None,
    when: datetime | None = None,
) -> str:
    when = when or datetime.now()
    if not articles:
        return f"Morning Brief — {when:%A, %B %d, %Y}\n\nNo stories met the bar today."

    lines = [f"Morning Brief — {when:%A, %B %d, %Y}", ""]

    # Top Stories: the highest-importance items across all topics.
    top = sorted(articles, key=lambda a: (a.importance or 0, a.rank_score or 0), reverse=True)[:5]
    lines.append("TOP STORIES")
    for i, article in enumerate(top, 1):
        imp = f" (importance {article.importance})" if article.importance else ""
        lines.append(f"{i}. [{article.topic}] {article.title}{_opinion_tag(article)}{imp}")
        lines.append(f"   {article.tldr or ''}".rstrip())
    lines.append("")

    # Per-topic sections (only topics that have articles), most important first.
    by_topic: "OrderedDict[str, list[Article]]" = OrderedDict()
    for article in sorted(articles, key=lambda a: (a.importance or 0), reverse=True):
        by_topic.setdefault(article.topic, []).append(article)

    for topic, topic_articles in by_topic.items():
        lines.append(topic.upper())
        for article in topic_articles:
            lines.append(f"- {article.title}{_opinion_tag(article)}")
            lines.append(f"  Source: {article.source}")
            if article.tldr:
                lines.append(f"  TLDR: {article.tldr}")
            if article.why_it_matters:
                lines.append(f"  Why it matters: {article.why_it_matters}")
            lines.append(f"  Link: {article.url}")
        lines.append("")

    watchlist = _build_watchlist(articles, clusters or [])
    if watchlist:
        lines.append("WATCHLIST")
        lines.extend(f"- {item}" for item in watchlist)
        lines.append("")

    return "\n".join(lines).rstrip()


def _build_watchlist(articles: list[Article], clusters: list[StoryCluster]) -> list[str]:
    items: list[str] = []

    # Likely to develop: highest-importance story with an explicit "what to watch".
    developing = sorted(
        (a for a in articles if a.what_to_watch_next),
        key=lambda a: (a.importance or 0),
        reverse=True,
    )
    if developing:
        a = developing[0]
        items.append(f"Likely to develop: {a.title} — {a.what_to_watch_next}")

    # Underreported: a meaningful story carried by only one source.
    source_counts: dict[str, int] = {}
    for a in articles:
        source_counts[a.source.lower()] = source_counts.get(a.source.lower(), 0) + 1
    underreported = [
        a for a in articles if (a.importance or 0) >= 7 and source_counts.get(a.source.lower(), 0) == 1
    ]
    if underreported:
        a = max(underreported, key=lambda x: x.importance or 0)
        if not developing or a.title != developing[0].title:
            items.append(f"Underreported: {a.title} ({a.source})")

    # Conflicting reports: any cluster covered by multiple outlets.
    conflicting = [c for c in clusters if c.conflicting_details]
    if conflicting:
        c = max(conflicting, key=lambda x: x.importance or 0)
        items.append(f"Conflicting reports: {c.title} — {c.conflicting_details}")

    return items
