"""Topic assignment and relevance checks for news articles."""

from __future__ import annotations

import logging

from app.config import TopicConfig
from app.services.pipeline.article import PipelineArticle
from app.services.pipeline.scorer import _topic_match

logger = logging.getLogger(__name__)

# Topics where RSS category tags alone are not enough.
_STRICT_TOPICS = {"us news", "finance", "markets"}

_FINANCE_SIGNALS = (
    "economy", "economic", "inflation", "fed ", "federal reserve", "interest rate",
    "central bank", "gdp", "recession", "treasury", "bond", "yield", "tariff",
    "trade deficit", "jobs report", "unemployment", "consumer price", "cpi",
    "earnings", "revenue", "profit", "bankruptcy", "merger", "acquisition",
    "wall street", "stock", "nasdaq", "s&p", "dow ", "ipo", "venture",
    "billion", "million shares", "quarterly", "fiscal", "budget", "debt ceiling",
    "mortgage", "housing market", "oil price", "commodity",
)

_MARKETS_SIGNALS = (
    "stock", "market", "nasdaq", "s&p", "dow", "wall street", "trading",
    "shares", "index", "futures", "earnings", "ipo", "rally", "selloff",
    "bull", "bear", "volatility", "investor", "portfolio", "etf",
)

_GEO_NEWS_SIGNALS = (
    "war ", "missile", "airstrike", "invasion", "military", "troops",
    "israel", "iran", "gaza", "ukraine", "russia", "nato", "g7 ",
    "summit", "protest", "ceasefire", "diplomat", "embassy", "sanctions",
    "peace deal", "world cup", "election fraud",
)


def _text(article: PipelineArticle) -> str:
    return f"{article.title} {article.description or ''}".lower()


def topic_fits(
    article: PipelineArticle,
    topic: TopicConfig,
    *,
    min_score: float = 0.2,
    for_topic: str | None = None,
) -> bool:
    """Whether a story belongs in this topic section."""
    score = _topic_match(article, topic)
    if score < 0:
        return False

    text = _text(article)
    name = topic.name.lower()
    tagged_as = (for_topic or article.topic).lower()

    if name == "us news":
        if score >= min_score:
            return True
        us_signals = (
            "united states", "u.s.", " us ", "u.s ", "congress", "white house",
            "supreme court", "american", "washington", "federal", "senate",
            "governor", "pentagon", "capitol", "fbi", "cia", "doj", "trump",
            "biden", "republican", "democrat",
        )
        return any(sig in text for sig in us_signals)

    if name == "finance":
        has_geo = any(sig in text for sig in _GEO_NEWS_SIGNALS)
        has_fin = any(sig in text for sig in _FINANCE_SIGNALS) or score >= min_score
        if has_geo and not has_fin:
            return False
        if tagged_as == name:
            return has_fin
        return has_fin and score >= min_score

    if name == "markets":
        has_geo = any(sig in text for sig in _GEO_NEWS_SIGNALS)
        has_mkt = any(sig in text for sig in _MARKETS_SIGNALS) or score >= min_score
        if has_geo and not has_mkt:
            return False
        if tagged_as == name:
            return has_mkt
        return has_mkt and score >= min_score

    if tagged_as == name:
        return True
    return score >= min_score


def reclassify_articles(
    articles: list[PipelineArticle],
    topics: list[TopicConfig],
) -> list[PipelineArticle]:
    """Reassign articles to the best-matching topic when the fetch tag is weak."""
    topic_map = {t.name: t for t in topics}
    changed = 0

    for article in articles:
        current = topic_map.get(article.topic)
        current_score = _topic_match(article, current) if current else -1.0
        if current_score < 0:
            current_score = 0.0

        best_name = article.topic
        best_score = current_score

        for topic in topics:
            if topic.name == article.topic:
                continue
            score = _topic_match(article, topic)
            if score < 0:
                continue
            # Require a clear win to move out of a strict bucket.
            margin = 0.35 if article.topic.lower() in _STRICT_TOPICS else 0.25
            if score > best_score + margin and topic_fits(article, topic, for_topic=topic.name):
                best_name = topic.name
                best_score = score

        if best_name != article.topic:
            article.topic = best_name
            changed += 1

    if changed:
        logger.info("Reclassified %d articles to a better topic", changed)
    return articles
