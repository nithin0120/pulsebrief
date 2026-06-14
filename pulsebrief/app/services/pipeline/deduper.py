"""Stage 3: local deduplication before any LLM calls."""

from __future__ import annotations

import logging

from rapidfuzz import fuzz

from app.services.pipeline.article import PipelineArticle

logger = logging.getLogger(__name__)


def deduplicate_articles(
    articles: list[PipelineArticle],
    *,
    fuzzy_title_threshold: int = 88,
    description_threshold: int = 90,
) -> list[PipelineArticle]:
    seen_canonical: set[str] = set()
    seen_urls: set[str] = set()
    unique: list[PipelineArticle] = []
    title_norms: list[str] = []
    descriptions: list[str] = []

    for article in articles:
        canon = article.canonical_url or article.url
        url_key = article.url.rstrip("/").lower()

        if canon in seen_canonical or url_key in seen_urls:
            continue

        tn = article.title_normalized
        if any(fuzz.token_set_ratio(tn, prev) >= fuzzy_title_threshold for prev in title_norms):
            continue

        desc = (article.description or "").lower()
        if desc and len(desc) > 60:
            if any(
                fuzz.token_set_ratio(desc, prev) >= description_threshold for prev in descriptions
            ):
                continue

        # Same source + very similar title
        for u in unique:
            if u.source.lower() == article.source.lower() and fuzz.ratio(
                u.title_normalized, tn
            ) >= 92:
                break
        else:
            seen_canonical.add(canon)
            seen_urls.add(url_key)
            title_norms.append(tn)
            if desc:
                descriptions.append(desc)
            unique.append(article)
            continue

    logger.info("Deduped %d -> %d articles", len(articles), len(unique))
    return unique
