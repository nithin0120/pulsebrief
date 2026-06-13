"""Rank and select articles for the daily digest."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from app.config import settings
from app.services.news_fetcher import RawArticle, is_reputable_source, normalize_title

logger = logging.getLogger(__name__)


def _keyword_overlap(title: str, description: str | None, keywords: list[str]) -> float:
    text = f"{title} {description or ''}".lower()
    if not keywords:
        return 0.5
    hits = sum(1 for kw in keywords if kw.lower() in text)
    return min(1.0, hits / max(len(keywords), 1))


def _recency_score(published_at: datetime | None) -> float:
    if not published_at:
        return 0.3
    hours_old = (datetime.utcnow() - published_at).total_seconds() / 3600
    if hours_old <= 6:
        return 1.0
    if hours_old <= 24:
        return 0.85
    if hours_old <= 48:
        return 0.6
    return 0.3


def _story_fingerprint(title: str) -> str:
    words = normalize_title(title).split()
    stop = {"the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "is", "are", "with"}
    significant = [w for w in words if w not in stop and len(w) > 2][:6]
    return " ".join(sorted(significant))


def score_article(article: RawArticle, topic_keywords: list[str]) -> float:
    recency = _recency_score(article.published_at)
    relevance = _keyword_overlap(article.title, article.description, topic_keywords)
    reputation = 1.0 if is_reputable_source(article.source) else 0.5
    has_description = 0.2 if article.description else 0.0
    base = recency * 0.35 + relevance * 0.35 + reputation * 0.25 + has_description
    # Opinion/analysis is useful but should not crowd out hard news.
    if article.is_opinion:
        base *= 0.85
    return base


class Ranker:
    def rank(
        self,
        articles: list[RawArticle],
        topic_keywords_map: dict[str, list[str]],
        prefs=None,
        memory=None,
    ) -> list[tuple[RawArticle, float]]:
        scored: list[tuple[RawArticle, float]] = []
        for article in articles:
            keywords = topic_keywords_map.get(article.topic, [])
            score = score_article(article, keywords)
            if prefs is not None:
                score *= prefs.source_penalty(article.source)
                score *= prefs.topic_boost(article.topic)
            if memory is not None:
                score *= memory.score_multiplier(
                    url=article.url, source=article.source, topic=article.topic
                )
            scored.append((article, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def select_for_digest(
        self,
        articles: list[RawArticle],
        topic_keywords_map: dict[str, list[str]],
        per_topic: int | None = None,
        total: int | None = None,
        prefs=None,
        memory=None,
    ) -> list[tuple[RawArticle, float]]:
        per_topic_cap = per_topic or settings.max_articles_per_topic
        total_cap = total or settings.max_total_articles
        max_per_source = settings.max_per_source

        scored = self.rank(articles, topic_keywords_map, prefs=prefs, memory=memory)
        per_topic_groups: dict[str, list[tuple[RawArticle, float]]] = defaultdict(list)
        for article, score in scored:
            per_topic_groups[article.topic].append((article, score))

        selected: list[tuple[RawArticle, float]] = []
        seen_fingerprints: set[str] = set()
        seen_sources: dict[str, int] = defaultdict(int)

        for topic, topic_articles in per_topic_groups.items():
            count = 0
            for article, score in topic_articles:
                if count >= per_topic_cap:
                    break
                fp = _story_fingerprint(article.title)
                if fp in seen_fingerprints:
                    continue
                # Hard cap on how many stories one outlet contributes.
                if seen_sources[article.source.lower()] >= max_per_source:
                    continue
                seen_fingerprints.add(fp)
                seen_sources[article.source.lower()] += 1
                selected.append((article, score))
                count += 1

        selected.sort(key=lambda x: x[1], reverse=True)
        trimmed = selected[:total_cap]

        logger.info(
            "Selected %d articles for digest (from %d candidates)",
            len(trimmed),
            len(articles),
        )
        return trimmed
