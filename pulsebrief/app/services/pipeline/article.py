"""Shared article and cluster dataclasses for the local pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PipelineArticle:
    title: str
    url: str
    source: str
    topic: str
    description: str | None = None
    content: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    canonical_url: str = ""
    title_normalized: str = ""
    source_domain: str = ""
    is_opinion: bool = False
    source_type: str = "unknown"  # newsapi | gdelt | rss | hn
    reputation: float = 0.6
    perspective: str | None = None
    importance_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    db_id: int | None = None  # set after DB persist

    @property
    def article_id(self) -> str:
        key = self.canonical_url or self.url
        return hashlib.sha1(key.encode()).hexdigest()[:16]


@dataclass
class StoryClusterData:
    cluster_id: str
    cluster_title: str
    topic: str
    articles: list[PipelineArticle]
    importance_score: float = 0.0
    source_count: int = 0
    source_names: list[str] = field(default_factory=list)
    source_diversity_score: float = 0.0
    earliest_published: datetime | None = None
    latest_published: datetime | None = None
    representative: PipelineArticle | None = None
    is_opinion: bool = False
    is_breaking: bool = False

    def __post_init__(self) -> None:
        if not self.representative and self.articles:
            self.representative = max(
                self.articles,
                key=lambda a: (a.importance_score, a.reputation),
            )
        if not self.source_names:
            self.source_names = sorted({a.source for a in self.articles})
        self.source_count = len(self.source_names)
        dates = [a.published_at for a in self.articles if a.published_at]
        if dates:
            self.earliest_published = min(dates)
            self.latest_published = max(dates)


@dataclass
class ClusterContext:
    """Compact context sent to Groq (target ~400-700 tokens per cluster)."""

    cluster_id: int
    topic: str
    cluster_title: str
    sources: list[str]
    urls: list[str]
    published_dates: list[str]
    titles: list[str]
    descriptions: list[str]
    extracted_key_sentences: list[str]
    source_count: int
    confidence_signals: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "topic": self.topic,
            "cluster_title": self.cluster_title,
            "sources": self.sources,
            "urls": self.urls,
            "published_dates": self.published_dates,
            "titles": self.titles,
            "descriptions": self.descriptions,
            "extracted_key_sentences": self.extracted_key_sentences,
            "source_count": self.source_count,
            "confidence_signals": self.confidence_signals,
        }
