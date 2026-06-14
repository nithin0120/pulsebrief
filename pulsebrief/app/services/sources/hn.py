"""Hacker News via Algolia API."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from app.config import TopicConfig
from app.services.pipeline.article import PipelineArticle
from app.services.sources.base import SourceConnector

logger = logging.getLogger(__name__)


class HnConnector(SourceConnector):
    async def fetch(self, topics: list[TopicConfig]) -> list[PipelineArticle]:
        url = self.source.url or "https://hn.algolia.com/api/v1/search?tags=front_page"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("HN fetch failed")
            return []

        topic = self.source.category if self.source.category != "mixed" else "Tech"
        articles: list[PipelineArticle] = []
        for hit in data.get("hits", [])[:30]:
            title = hit.get("title") or hit.get("story_title") or "Untitled"
            link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            if not link.startswith("http"):
                continue
            ts = hit.get("created_at_i")
            published = datetime.utcfromtimestamp(ts) if ts else None
            articles.append(
                PipelineArticle(
                    title=title,
                    url=link,
                    source="Hacker News",
                    topic=topic,
                    description=None,
                    published_at=published,
                    source_type="hn",
                    reputation=self.source.reputation,
                )
            )
        logger.info("HN: %d articles", len(articles))
        return articles
