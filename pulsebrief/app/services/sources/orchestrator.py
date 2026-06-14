"""Stage 1: multi-source fetch orchestrator."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import SourceConfig, TopicConfig, load_config, load_sources
from app.models import Article
from app.services.pipeline.article import PipelineArticle
from app.services.sources.aggregators import GdeltConnector, NewsApiConnector
from app.services.sources.hn import HnConnector
from app.services.sources.rss import RssConnector

logger = logging.getLogger(__name__)

_CONNECTORS = {
    "newsapi": NewsApiConnector,
    "gdelt": GdeltConnector,
    "rss": RssConnector,
    "hn": HnConnector,
}


class FetchOrchestrator:
    def __init__(self, db: Session | None = None, config: dict | None = None) -> None:
        self.db = db
        self.config = config or load_config()
        self.max_total = int(self.config.get("fetch", {}).get("max_total_articles", 300))
        self.skip_hours = int(self.config.get("fetch", {}).get("refetch_skip_hours", 6))

    def _recent_urls(self) -> set[str]:
        if not self.db:
            return set()
        since = datetime.utcnow() - timedelta(hours=self.skip_hours)
        rows = (
            self.db.query(Article.canonical_url, Article.url)
            .filter(Article.fetched_at >= since)
            .all()
        )
        urls: set[str] = set()
        for canon, url in rows:
            if canon:
                urls.add(canon)
            if url:
                urls.add(url.rstrip("/").lower())
        return urls

    def _connector_for(self, source: SourceConfig):
        cls = _CONNECTORS.get(source.type)
        if not cls:
            logger.warning("Unknown source type '%s' for %s", source.type, source.name)
            return None
        return cls(source)

    async def fetch_all(self, topics: list[TopicConfig]) -> list[PipelineArticle]:
        sources = load_sources()
        seen_types: set[str] = set()
        all_articles: list[PipelineArticle] = []
        recent = self._recent_urls()

        for source in sources:
            # Only one newsapi and one gdelt connector instance
            if source.type in ("newsapi", "gdelt"):
                if source.type in seen_types:
                    continue
                seen_types.add(source.type)

            connector = self._connector_for(source)
            if not connector:
                continue
            try:
                batch = await connector.fetch(topics)
                all_articles.extend(batch)
                logger.info("Source %s (%s): %d articles", source.name, source.type, len(batch))
            except Exception:
                logger.exception("Source %s failed", source.name)

        # Skip recently seen URLs
        if recent:
            before = len(all_articles)
            all_articles = [
                a
                for a in all_articles
                if (a.canonical_url or a.url) not in recent
                and a.url.rstrip("/").lower() not in recent
            ]
            logger.info("Skipped %d recently-fetched URLs", before - len(all_articles))

        # Cap total
        all_articles = all_articles[: self.max_total]
        logger.info("Fetched %d total articles across sources", len(all_articles))
        return all_articles
