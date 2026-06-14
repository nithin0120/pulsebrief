"""RSS feed ingestion via feedparser."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from app.config import TopicConfig
from app.services.pipeline.article import PipelineArticle
from app.services.sources.base import SourceConnector

logger = logging.getLogger(__name__)
USER_AGENT = "PulseBrief/1.0 (local news digest)"


def _parse_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            try:
                return datetime(*tp[:6])
            except (TypeError, ValueError):
                pass
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo:
                    return dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
            except (TypeError, ValueError):
                continue
    return None


class RssConnector(SourceConnector):
    async def fetch(self, topics: list[TopicConfig]) -> list[PipelineArticle]:
        url = self.source.url
        if not url:
            return []
        try:
            async with httpx.AsyncClient(
                timeout=20.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
        except Exception:
            logger.exception("RSS fetch failed for %s", self.source.name)
            return []

        topic = self.source.category if self.source.category != "mixed" else "World News"
        articles: list[PipelineArticle] = []
        for entry in parsed.entries[:30]:
            link = getattr(entry, "link", None) or ""
            title = getattr(entry, "title", None) or "Untitled"
            if not link.startswith("http"):
                continue
            desc = getattr(entry, "summary", None) or getattr(entry, "description", None)
            author = getattr(entry, "author", None)
            articles.append(
                PipelineArticle(
                    title=title,
                    url=link,
                    source=self.source.name,
                    topic=topic,
                    description=desc,
                    author=author,
                    published_at=_parse_date(entry),
                    source_type="rss",
                    reputation=self.source.reputation,
                    perspective=self.source.perspective,
                )
            )
        logger.info("RSS %s: %d articles", self.source.name, len(articles))
        return articles
