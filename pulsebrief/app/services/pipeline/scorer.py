"""Stage 4: cheap local importance scoring (no LLM)."""

from __future__ import annotations

import logging
import math
from datetime import datetime

from app.config import TopicConfig, load_config
from app.services.pipeline.article import PipelineArticle

logger = logging.getLogger(__name__)

# High-signal keyword categories for importance boosts.
IMPORTANCE_KEYWORDS: dict[str, list[str]] = {
    "geopolitical": [
        "war", "invasion", "sanctions", "missile", "ceasefire", "nato", "treaty",
        "diplomacy", "embassy", "coup",
    ],
    "elections": ["election", "vote", "ballot", "primary", "poll", "campaign"],
    "regulation": ["regulation", "ban", "antitrust", "sec ", "fda", "legislation", "bill passed"],
    "markets": [
        "market crash", "rally", "recession", "earnings", "ipo", "fed ", "interest rate",
        "inflation", "central bank",
    ],
    "cyber": [
        "data breach", "ransomware", "cyberattack", "vulnerability", "cve-", "exploit",
        "zero-day", "malware",
    ],
    "ai": [
        "artificial intelligence", "openai", "anthropic", "llm", "model release",
        "generative ai", "chatgpt",
    ],
    "legal": ["lawsuit", "indicted", "investigation", "settlement", "verdict", "court ruling"],
    "health": ["outbreak", "pandemic", "vaccine", "who ", "public health emergency"],
    "climate": ["hurricane", "wildfire", "flood", "earthquake", "climate disaster"],
}

ENTERTAINMENT_KEYWORDS = [
    "celebrity", "kardashian", "taylor swift", "reality tv", "gossip", "dating",
    "divorce rumor", "red carpet",
]

INTERNATIONAL_PERSPECTIVES = {
    "middle east", "uk/global", "uk/left-of-center", "global", "europe", "asia",
}


def _recency_score(published_at: datetime | None, half_life_hours: float) -> float:
    if not published_at:
        return 0.25
    hours = max(0.0, (datetime.utcnow() - published_at).total_seconds() / 3600)
    return math.exp(-0.693 * hours / max(half_life_hours, 1))


def _keyword_importance(text: str) -> float:
    text_l = text.lower()
    hits = 0
    for keywords in IMPORTANCE_KEYWORDS.values():
        hits += sum(1 for kw in keywords if kw in text_l)
    penalty = sum(1 for kw in ENTERTAINMENT_KEYWORDS if kw in text_l)
    return max(0.0, min(3.0, hits * 0.35 - penalty * 0.8))


def _topic_match(article: PipelineArticle, topic: TopicConfig) -> float:
    text = f"{article.title} {article.description or ''}".lower()
    if topic.negative_keywords and any(nk.lower() in text for nk in topic.negative_keywords):
        return -2.0
    kws = topic.keywords or topic.queries
    hits = sum(1 for kw in kws if kw.lower() in text)
    return min(2.0, hits / max(len(kws), 1) * 2.0)


def score_article(
    article: PipelineArticle,
    topics: list[TopicConfig],
    *,
    prefs=None,
    memory=None,
    config: dict | None = None,
) -> float:
    cfg = (config or load_config()).get("scoring", {})
    weights = cfg.get("weights", {})
    half_life = float(cfg.get("recency_half_life_hours", 18))
    low_quality = {s.lower() for s in cfg.get("low_quality_sources", [])}

    topic_cfg = next((t for t in topics if t.name == article.topic), None)
    topic_priority = (topic_cfg.priority if topic_cfg else 3) / 5.0
    topic_relevance = _topic_match(article, topic_cfg) if topic_cfg else 0.5

    text = f"{article.title} {article.description or ''}"
    score = 0.0
    score += weights.get("topic_priority", 1.0) * topic_priority
    score += weights.get("source_reputation", 1.2) * article.reputation
    score += weights.get("recency", 1.5) * _recency_score(article.published_at, half_life)
    score += weights.get("keyword_importance", 1.3) * _keyword_importance(text)
    score += weights.get("topic_priority", 1.0) * topic_relevance * 0.5

    if article.perspective and article.perspective.lower() in INTERNATIONAL_PERSPECTIVES:
        score += weights.get("international_bonus", 0.8)

    if article.is_opinion:
        score -= weights.get("opinion_penalty", 0.6)

    if any(lq in article.source.lower() for lq in low_quality):
        score -= weights.get("low_quality_source_penalty", 1.5)

    if prefs is not None:
        if prefs.is_muted(article.title, article.description, article.source):
            score -= weights.get("muted_keyword_penalty", 5.0)
        score *= prefs.source_penalty(article.source)
        score *= prefs.topic_boost(article.topic)

    if memory is not None:
        score *= memory.score_multiplier(
            url=article.url, source=article.source, topic=article.topic
        )

    if topic_cfg and topic_cfg.source_preferences:
        if any(pref.lower() in article.source.lower() for pref in topic_cfg.source_preferences):
            score += 0.4

    article.importance_score = round(max(0.0, score), 3)
    return article.importance_score


def score_all(
    articles: list[PipelineArticle],
    topics: list[TopicConfig],
    *,
    prefs=None,
    memory=None,
    config: dict | None = None,
) -> list[PipelineArticle]:
    for a in articles:
        score_article(a, topics, prefs=prefs, memory=memory, config=config)
    articles.sort(key=lambda x: x.importance_score, reverse=True)
    logger.info("Scored %d articles (top score %.2f)", len(articles), articles[0].importance_score if articles else 0)
    return articles
