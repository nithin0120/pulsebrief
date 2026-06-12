"""Fetch news articles from NewsAPI with GDELT fallback."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.config import TopicConfig, settings

logger = logging.getLogger(__name__)

REPUTABLE_SOURCES = {
    "reuters", "associated press", "ap news", "bbc", "bbc news", "the guardian",
    "the new york times", "nytimes", "wall street journal", "wsj", "financial times",
    "bloomberg", "the economist", "npr", "pbs", "al jazeera", "cnn", "the washington post",
    "politico", "axios", "nature", "science", "techcrunch", "arstechnica", "wired",
    "the verge", "mit technology review", "hindustan times", "the indian express",
}

USER_AGENT = "PulseBrief/1.0 (local news digest)"


@dataclass
class RawArticle:
    title: str
    url: str
    source: str
    description: str | None
    content: str | None
    topic: str
    published_at: datetime | None


def normalize_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", title.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def titles_are_similar(a: str, b: str, threshold: float = 0.82) -> bool:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio() >= threshold


def is_reputable_source(source: str) -> bool:
    return source.lower().strip() in REPUTABLE_SOURCES or any(
        rep in source.lower() for rep in REPUTABLE_SOURCES
    )


def deduplicate_articles(articles: list[RawArticle]) -> list[RawArticle]:
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    unique: list[RawArticle] = []

    for article in articles:
        url_key = article.url.rstrip("/").lower()
        if url_key in seen_urls:
            continue

        if any(titles_are_similar(article.title, t) for t in seen_titles):
            continue

        seen_urls.add(url_key)
        seen_titles.append(article.title)
        unique.append(article)

    logger.info("Deduplicated %d -> %d articles", len(articles), len(unique))
    return unique


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


class NewsFetcher:
    def __init__(self) -> None:
        self.use_newsapi = bool(settings.news_api_key)

    async def fetch_for_topics(self, topics: list[TopicConfig]) -> list[RawArticle]:
        all_articles: list[RawArticle] = []
        async with httpx.AsyncClient(
            timeout=45.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            for i, topic in enumerate(topics):
                if i > 0 and not self.use_newsapi:
                    await asyncio.sleep(3.0)
                try:
                    if self.use_newsapi:
                        articles = await self._fetch_newsapi(client, topic)
                    else:
                        articles = await self._fetch_gdelt(client, topic)
                    all_articles.extend(articles)
                    logger.info("Fetched %d articles for topic '%s'", len(articles), topic.name)
                except Exception:
                    logger.exception("Failed to fetch articles for topic '%s'", topic.name)
        return deduplicate_articles(all_articles)

    async def _fetch_newsapi(
        self, client: httpx.AsyncClient, topic: TopicConfig
    ) -> list[RawArticle]:
        # Build a proper boolean OR query, quoting multi-word terms as phrases.
        terms = topic.keywords or topic.queries
        query = " OR ".join(f'"{t}"' if " " in t else t for t in terms[:6])
        since = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")

        params = {
            "q": query,
            "from": since,
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": 20,
            "apiKey": settings.news_api_key,
        }
        resp = await client.get("https://newsapi.org/v2/everything", params=params)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            raise RuntimeError(data.get("message", "NewsAPI error"))

        return [self._parse_newsapi_item(item, topic.name) for item in data.get("articles", [])]

    def _parse_newsapi_item(self, item: dict[str, Any], topic: str) -> RawArticle:
        source_name = "Unknown"
        if isinstance(item.get("source"), dict):
            source_name = item["source"].get("name") or "Unknown"
        return RawArticle(
            title=item.get("title") or "Untitled",
            url=item.get("url") or "",
            source=source_name,
            description=item.get("description"),
            content=item.get("content"),
            topic=topic,
            published_at=_parse_datetime(item.get("publishedAt")),
        )

    async def _fetch_gdelt(
        self, client: httpx.AsyncClient, topic: TopicConfig
    ) -> list[RawArticle]:
        # GDELT works best with short single-term queries
        query = topic.queries[0] if topic.queries else topic.name
        if " OR " in query:
            query = query.split(" OR ")[0].strip()
        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": "25",
            "format": "json",
            "sort": "DateDesc",
            "timespan": "48h",
        }
        url = f"https://api.gdeltproject.org/api/v2/doc/doc?{self._encode_params(params)}"
        data = await self._get_json_with_retry(client, url)
        if not data:
            return []

        articles: list[RawArticle] = []
        for item in data.get("articles", []):
            title = item.get("title") or "Untitled"
            article_url = item.get("url") or item.get("socialimage") or ""
            if not article_url or not article_url.startswith("http"):
                continue
            articles.append(
                RawArticle(
                    title=title,
                    url=article_url,
                    source=item.get("domain") or item.get("sourcecountry") or "Unknown",
                    description=None,
                    content=None,
                    topic=topic.name,
                    published_at=_parse_gdelt_date(item.get("seendate")),
                )
            )
        return articles

    @staticmethod
    def _encode_params(params: dict[str, str]) -> str:
        return "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())

    async def _get_json_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        max_attempts: int = 3,
    ) -> dict[str, Any] | None:
        for attempt in range(max_attempts):
            try:
                resp = await client.get(url)
                if resp.status_code == 429:
                    if attempt >= max_attempts - 1:
                        logger.warning("GDELT rate limited; skipping after %d attempts", max_attempts)
                        return None
                    wait = 3 * (attempt + 1)
                    logger.warning("GDELT rate limited; retrying in %ds", wait)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                if not resp.content:
                    return None
                return resp.json()
            except httpx.RemoteProtocolError:
                wait = 2 ** attempt
                logger.warning("GDELT connection error; retrying in %ds", wait)
                await asyncio.sleep(wait)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500 and attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
            except ValueError:
                wait = 2 ** attempt
                logger.warning("GDELT returned invalid JSON; retrying in %ds", wait)
                await asyncio.sleep(wait)
        return None


def _parse_gdelt_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(value[:15] if "T" in fmt else value[:14], fmt)
        except ValueError:
            continue
    return None
