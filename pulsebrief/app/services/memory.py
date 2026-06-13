"""Local interaction memory and summary-failure queue (SQLite-backed)."""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy.orm import Session

from app.models import ArticleInteraction, SummaryFailure

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"saved", "ignored", "clicked"}


def record_interaction(
    db: Session,
    *,
    article_url: str,
    action: str,
    title_normalized: str | None = None,
    source: str | None = None,
    topic: str | None = None,
) -> None:
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unknown interaction '{action}'. Use one of {sorted(VALID_ACTIONS)}.")
    db.add(
        ArticleInteraction(
            article_url=article_url,
            action=action,
            title_normalized=title_normalized,
            source=source,
            topic=topic,
        )
    )
    db.commit()
    logger.info("Recorded interaction '%s' for %s", action, article_url[:80])


class InteractionMemory:
    """Aggregates past interactions into ranking signals."""

    def __init__(self, db: Session) -> None:
        rows = db.query(ArticleInteraction).all()
        self.ignored_sources: Counter[str] = Counter()
        self.saved_topics: Counter[str] = Counter()
        self.ignored_urls: set[str] = set()
        for row in rows:
            if row.action == "ignored":
                self.ignored_urls.add(row.article_url)
                if row.source:
                    self.ignored_sources[row.source.lower()] += 1
            elif row.action in ("saved", "clicked") and row.topic:
                self.saved_topics[row.topic.lower()] += 1

    def score_multiplier(self, *, url: str, source: str, topic: str) -> float:
        mult = 1.0
        if url in self.ignored_urls:
            mult *= 0.4
        if self.ignored_sources.get(source.lower(), 0) >= 2:
            mult *= 0.7
        if self.saved_topics.get(topic.lower(), 0) >= 2:
            mult *= 1.2
        return mult


def queue_summary_failure(
    db: Session, *, title: str, url: str, provider: str | None, error: str
) -> None:
    db.add(SummaryFailure(title=title[:512], url=url, provider=provider, error=error[:1000]))
    db.commit()


def recent_failures(db: Session, limit: int = 20) -> list[SummaryFailure]:
    return (
        db.query(SummaryFailure)
        .filter(SummaryFailure.resolved == False)  # noqa: E712
        .order_by(SummaryFailure.created_at.desc())
        .limit(limit)
        .all()
    )
