"""NewsAPI and GDELT connectors wrapping existing fetcher logic."""

from __future__ import annotations

import logging

from app.config import TopicConfig, settings
from app.services.news_fetcher import NewsFetcher, RawArticle
from app.services.pipeline.article import PipelineArticle
from app.services.sources.base import SourceConnector

logger = logging.getLogger(__name__)


def _raw_to_pipeline(raw: RawArticle, source_type: str, reputation: float, perspective: str | None) -> PipelineArticle:
    return PipelineArticle(
        title=raw.title,
        url=raw.url,
        source=raw.source,
        topic=raw.topic,
        description=raw.description,
        content=raw.content,
        published_at=raw.published_at,
        canonical_url=raw.canonical_url,
        is_opinion=raw.is_opinion,
        source_type=source_type,
        reputation=reputation,
        perspective=perspective,
    )


class NewsApiConnector(SourceConnector):
    async def fetch(self, topics: list[TopicConfig]) -> list[PipelineArticle]:
        if not settings.news_api_key:
            return []
        fetcher = NewsFetcher()
        if not fetcher.use_newsapi:
            return []
        raws = await fetcher.fetch_for_topics(topics)
        return [
            _raw_to_pipeline(r, "newsapi", self.source.reputation, self.source.perspective)
            for r in raws
        ]


class GdeltConnector(SourceConnector):
    """GDELT supplement — when NewsAPI is available, only world/international topics."""

    _SUPPLEMENT_TOPICS = {"world news", "geopolitics", "international", "science"}

    async def fetch(self, topics: list[TopicConfig]) -> list[PipelineArticle]:
        if settings.news_api_key:
            topics = [t for t in topics if t.name.lower() in self._SUPPLEMENT_TOPICS]
            if not topics:
                return []
        fetcher = NewsFetcher()
        original = fetcher.use_newsapi
        fetcher.use_newsapi = False
        try:
            raws = await fetcher.fetch_for_topics(topics)
        finally:
            fetcher.use_newsapi = original
        return [
            _raw_to_pipeline(r, "gdelt", self.source.reputation, self.source.perspective or "global")
            for r in raws
        ]
