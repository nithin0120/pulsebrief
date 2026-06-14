"""Stage 2: normalize articles for matching and scoring."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.services.news_fetcher import (
    detect_opinion,
    normalize_source,
    normalize_title,
)
from app.services.pipeline.article import PipelineArticle

_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref", "cmpid", "icid")
_TITLE_SUFFIX_RE = re.compile(
    r"\s*[-|–—]\s*(Reuters|Associated Press|AP News|BBC(?: News)?|CNN|"
    r"The Guardian|NPR|Al Jazeera|Bloomberg|CNBC|TechCrunch|The Verge|"
    r"BleepingComputer|The Hacker News|Ars Technica|Wired)\s*$",
    re.IGNORECASE,
)


def canonicalize_url(url: str) -> str:
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


def source_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except ValueError:
        return ""


def strip_title_suffix(title: str) -> str:
    return _TITLE_SUFFIX_RE.sub("", title).strip()


def normalize_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def normalize_article(article: PipelineArticle) -> PipelineArticle:
    article.url = (article.url or "").strip()
    article.canonical_url = canonicalize_url(article.url)
    article.source_domain = source_domain(article.url)
    article.title = strip_title_suffix((article.title or "Untitled").strip())
    article.title_normalized = normalize_title(article.title)
    article.source = normalize_source(article.source, article.url)
    article.published_at = normalize_timestamp(article.published_at)
    article.fetched_at = normalize_timestamp(article.fetched_at) or datetime.utcnow()
    if not article.is_opinion:
        article.is_opinion = detect_opinion(article.title, article.url)
    article.description = (article.description or "").strip() or None
    return article


def normalize_all(articles: list[PipelineArticle]) -> list[PipelineArticle]:
    return [normalize_article(a) for a in articles if a.url]
