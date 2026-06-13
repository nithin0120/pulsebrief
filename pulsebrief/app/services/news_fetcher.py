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

from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import httpx

from app.config import TopicConfig, settings

logger = logging.getLogger(__name__)

# Query params that are tracking noise; stripping them helps URL dedup.
_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref", "cmpid", "icid")
_OPINION_MARKERS = ("opinion", "op-ed", "editorial", "analysis", "commentary", "/opinion/")

REPUTABLE_SOURCES = {
    "reuters", "associated press", "ap news", "bbc", "bbc news", "the guardian",
    "the new york times", "nytimes", "wall street journal", "wsj", "financial times",
    "bloomberg", "the economist", "npr", "pbs", "al jazeera", "cnn", "the washington post",
    "politico", "axios", "nature", "science", "techcrunch", "arstechnica", "wired",
    "the verge", "mit technology review", "hindustan times", "the indian express",
}

USER_AGENT = "PulseBrief/1.0 (local news digest)"

# Map registrable domains to clean, human outlet names. Covers the messy/bare
# source names NewsAPI and GDELT often return (e.g. "Internet", "Biztoc.com").
SOURCE_DOMAIN_NAMES = {
    "thehackernews.com": "The Hacker News",
    "bleepingcomputer.com": "BleepingComputer",
    "biztoc.com": "Biztoc",
    "slashdot.org": "Slashdot",
    "arstechnica.com": "Ars Technica",
    "theverge.com": "The Verge",
    "techcrunch.com": "TechCrunch",
    "wired.com": "Wired",
    "engadget.com": "Engadget",
    "venturebeat.com": "VentureBeat",
    "gizmodo.com": "Gizmodo",
    "reuters.com": "Reuters",
    "apnews.com": "Associated Press",
    "bbc.com": "BBC",
    "bbc.co.uk": "BBC",
    "nytimes.com": "The New York Times",
    "wsj.com": "The Wall Street Journal",
    "ft.com": "Financial Times",
    "bloomberg.com": "Bloomberg",
    "theguardian.com": "The Guardian",
    "cnbc.com": "CNBC",
    "cnn.com": "CNN",
    "washingtonpost.com": "The Washington Post",
    "aljazeera.com": "Al Jazeera",
    "nypost.com": "New York Post",
    "financialpost.com": "Financial Post",
    "cryptobriefing.com": "Crypto Briefing",
    "rawstory.com": "Raw Story",
    "espn.com": "ESPN",
    "politico.com": "Politico",
    "axios.com": "Axios",
    "npr.org": "NPR",
    "businessinsider.com": "Business Insider",
    "forbes.com": "Forbes",
    "marketwatch.com": "MarketWatch",
    "yahoo.com": "Yahoo News",
}

# Source names that carry no information; derive a name from the URL instead.
GENERIC_SOURCE_NAMES = {"", "unknown", "internet", "google news", "[removed]", "rss", "news"}


def _registrable_domain(url: str) -> str:
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


# Second-level labels in multi-part public suffixes (e.g. ".co.uk", ".com.au").
_PUBLIC_SLDS = {"co", "com", "org", "net", "gov", "ac", "edu", "gob", "or"}


def _prettify_domain(domain: str) -> str:
    parts = [p for p in domain.split(".") if p]
    if len(parts) >= 3 and parts[-2] in _PUBLIC_SLDS:
        label = parts[-3]
    elif len(parts) >= 2:
        label = parts[-2]
    else:
        label = parts[0] if parts else ""
    return label.replace("-", " ").title() if label else "Unknown"


def normalize_source(raw_name: str | None, url: str) -> str:
    """Return a clean outlet name, mapping/derived from the domain when needed."""
    domain = _registrable_domain(url)
    if domain in SOURCE_DOMAIN_NAMES:
        return SOURCE_DOMAIN_NAMES[domain]
    for known, name in SOURCE_DOMAIN_NAMES.items():
        if domain == known or domain.endswith("." + known):
            return name

    cleaned = (raw_name or "").strip()
    looks_like_domain = "." in cleaned and " " not in cleaned
    if not cleaned or cleaned.lower() in GENERIC_SOURCE_NAMES or looks_like_domain:
        return _prettify_domain(domain) if domain else (cleaned or "Unknown")
    return cleaned


@dataclass
class RawArticle:
    title: str
    url: str
    source: str
    description: str | None
    content: str | None
    topic: str
    published_at: datetime | None
    canonical_url: str = ""
    is_opinion: bool = False

    def __post_init__(self) -> None:
        if not self.canonical_url:
            self.canonical_url = canonicalize_url(self.url)
        if not self.is_opinion:
            self.is_opinion = detect_opinion(self.title, self.url)
        self.source = normalize_source(self.source, self.url)


def normalize_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", title.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def canonicalize_url(url: str) -> str:
    """Normalize a URL for dedup: lowercase host, drop fragments/tracking params."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip().lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    ]
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), netloc, path, "", urlencode(kept), ""))


def detect_opinion(title: str, url: str) -> bool:
    haystack = f"{title.lower()} {url.lower()}"
    return any(marker in haystack for marker in _OPINION_MARKERS)


def text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def titles_are_similar(a: str, b: str, threshold: float = 0.82) -> bool:
    return text_similarity(normalize_title(a), normalize_title(b)) >= threshold


def is_reputable_source(source: str) -> bool:
    return source.lower().strip() in REPUTABLE_SOURCES or any(
        rep in source.lower() for rep in REPUTABLE_SOURCES
    )


def deduplicate_articles(articles: list[RawArticle]) -> list[RawArticle]:
    """Drop duplicate stories by canonical URL, near-identical titles, or
    highly similar descriptions (the same wire story republished widely)."""
    seen_canonical: set[str] = set()
    seen_titles: list[str] = []
    seen_descriptions: list[str] = []
    unique: list[RawArticle] = []

    for article in articles:
        canon = article.canonical_url or article.url.rstrip("/").lower()
        if canon in seen_canonical:
            continue
        if any(titles_are_similar(article.title, t) for t in seen_titles):
            continue
        desc = (article.description or "").strip().lower()
        if desc and len(desc) > 60 and any(
            text_similarity(desc, prev) >= 0.9 for prev in seen_descriptions
        ):
            continue

        seen_canonical.add(canon)
        seen_titles.append(article.title)
        if desc:
            seen_descriptions.append(desc)
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
        data = await self._newsapi_get_with_retry(client, params)
        if data is None:
            return []

        if data.get("status") != "ok":
            # rateLimited / maximumResultsReached etc. — log and skip, don't crash the run.
            logger.warning("NewsAPI non-ok for '%s': %s", topic.name, data.get("message"))
            return []

        return [self._parse_newsapi_item(item, topic.name) for item in data.get("articles", [])]

    async def _newsapi_get_with_retry(
        self, client: httpx.AsyncClient, params: dict, max_attempts: int = 3
    ) -> dict[str, Any] | None:
        for attempt in range(max_attempts):
            try:
                resp = await client.get("https://newsapi.org/v2/everything", params=params)
                if resp.status_code == 429:
                    if attempt >= max_attempts - 1:
                        logger.warning("NewsAPI rate limited; giving up for this topic")
                        return None
                    wait = 2 ** (attempt + 1)
                    logger.warning("NewsAPI rate limited; retrying in %ds", wait)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500 and attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.warning("NewsAPI HTTP error: %s", exc)
                return None
            except (httpx.RequestError, ValueError) as exc:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.warning("NewsAPI request failed: %s", exc)
                return None
        return None

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
