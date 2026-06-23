"""Stage 6: rank and select top story clusters for the final brief."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from rapidfuzz import fuzz

from app.config import TopicConfig, load_config
from app.services.pipeline.article import PipelineArticle, StoryClusterData
from app.services.pipeline.scorer import _topic_match
from app.services.pipeline.topic_classifier import topic_fits

logger = logging.getLogger(__name__)

INTERNATIONAL_TOPICS = {"world news", "geopolitics", "international"}
_FRESH_TOPICS = {"finance", "markets"}
_ROTATE_TOPICS = {"finance", "markets", "world news", "us news", "tech"}


def _cluster_key(title: str) -> str:
    return hashlib.sha1(title.lower().encode()).hexdigest()[:16]


def _singleton_cluster(article: PipelineArticle) -> StoryClusterData:
    return StoryClusterData(
        cluster_id=_cluster_key(article.title),
        cluster_title=article.title,
        topic=article.topic,
        articles=[article],
        importance_score=article.importance_score,
        is_opinion=article.is_opinion,
    )


def _headline_repeated(headline: str, previous: str | None) -> bool:
    if not previous:
        return False
    return fuzz.token_set_ratio(headline, previous) >= 88


def _hours_since(published_at: datetime | None) -> float | None:
    if not published_at:
        return None
    return max(0.0, (datetime.utcnow() - published_at).total_seconds() / 3600)


def _leader_rank_key(cluster: StoryClusterData) -> tuple[float, float, float]:
    """Higher is better: importance, recency boost, source count."""
    rep = cluster.representative or (cluster.articles[0] if cluster.articles else None)
    hours = _hours_since(rep.published_at if rep else None)
    recency = 0.0
    if hours is not None:
        if hours <= 6:
            recency = 3.0
        elif hours <= 12:
            recency = 2.0
        elif hours <= 24:
            recency = 1.0
        elif hours > 48:
            recency = -1.0
    return (cluster.importance_score + recency, recency, float(cluster.source_count))


class ClusterRanker:
    def __init__(self, config: dict | None = None) -> None:
        cfg = (config or load_config()).get("ranking", {})
        self.max_final = int(cfg.get("max_final_clusters", 8))
        self.min_final = int(cfg.get("min_final_clusters", 3))
        self.max_per_topic = int(cfg.get("max_clusters_per_topic", 2))
        self.max_per_source = int(cfg.get("max_articles_per_source", 2))
        self.require_international = bool(cfg.get("require_international", True))
        self.ensure_topic_diversity = bool(cfg.get("ensure_topic_diversity", True))

    @staticmethod
    def _cluster_score(cluster: StoryClusterData) -> tuple[float, int, float]:
        return (
            cluster.importance_score,
            cluster.source_count,
            cluster.source_diversity_score,
        )

    def _source_overloaded(self, cluster: StoryClusterData, source_counts: dict[str, int]) -> bool:
        for src in cluster.source_names:
            if source_counts.get(src.lower(), 0) >= self.max_per_source:
                return True
        return False

    def _try_add(
        self,
        cluster: StoryClusterData,
        selected: list[StoryClusterData],
        topic_counts: dict[str, int],
        topic_caps: dict[str, int],
        source_counts: dict[str, int],
    ) -> bool:
        if cluster in selected:
            return False
        cap = topic_caps.get(cluster.topic, self.max_per_topic)
        if topic_counts.get(cluster.topic, 0) >= cap and not cluster.is_breaking:
            return False
        if self._source_overloaded(cluster, source_counts) and not cluster.is_breaking:
            return False
        selected.append(cluster)
        topic_counts[cluster.topic] = topic_counts.get(cluster.topic, 0) + 1
        for src in cluster.source_names:
            source_counts[src.lower()] = source_counts.get(src.lower(), 0) + 1
        return True

    def _select_diverse(
        self,
        ranked: list[StoryClusterData],
        topics: list[TopicConfig],
        topic_caps: dict[str, int],
    ) -> list[StoryClusterData]:
        """Round-robin across topics so the brief covers multiple categories."""
        topic_order = [t.name for t in sorted(topics, key=lambda t: t.priority, reverse=True)]
        by_topic: dict[str, list[StoryClusterData]] = {}
        for cluster in ranked:
            by_topic.setdefault(cluster.topic, []).append(cluster)

        selected: list[StoryClusterData] = []
        topic_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}

        for round_num in range(self.max_per_topic):
            if len(selected) >= self.max_final:
                break
            for topic_name in topic_order:
                if len(selected) >= self.max_final:
                    break
                candidates = by_topic.get(topic_name, [])
                if round_num >= len(candidates):
                    continue
                self._try_add(
                    candidates[round_num],
                    selected,
                    topic_counts,
                    topic_caps,
                    source_counts,
                )

        for cluster in ranked:
            if len(selected) >= self.max_final:
                break
            self._try_add(cluster, selected, topic_counts, topic_caps, source_counts)

        return selected

    def select(
        self,
        clusters: list[StoryClusterData],
        topics: list[TopicConfig],
    ) -> list[StoryClusterData]:
        topic_caps = {t.name: t.max_clusters for t in topics}
        topic_priority = {t.name: t.priority for t in topics}

        ranked = sorted(clusters, key=self._cluster_score, reverse=True)

        if self.ensure_topic_diversity:
            selected = self._select_diverse(ranked, topics, topic_caps)
        else:
            selected = []
            topic_counts: dict[str, int] = {}
            source_counts: dict[str, int] = {}
            for cluster in ranked:
                if len(selected) >= self.max_final:
                    break
                self._try_add(cluster, selected, topic_counts, topic_caps, source_counts)

        if self.require_international and selected:
            has_intl = any(
                c.topic.lower() in INTERNATIONAL_TOPICS
                or any(
                    (a.perspective or "").lower() in {"global", "middle east", "uk/global"}
                    for a in c.articles
                )
                for c in selected
            )
            if not has_intl:
                for cluster in ranked:
                    if cluster in selected:
                        continue
                    if cluster.topic.lower() in INTERNATIONAL_TOPICS:
                        if len(selected) >= self.max_final:
                            selected[-1] = cluster
                        else:
                            selected.append(cluster)
                        break

        # Order by topic priority (not raw importance) so Groq sees a balanced mix.
        selected.sort(
            key=lambda c: (-topic_priority.get(c.topic, 0), -c.importance_score),
        )
        topics_represented = len({c.topic for c in selected})
        logger.info(
            "Selected %d finalist clusters from %d (%d topics)",
            len(selected),
            len(clusters),
            topics_represented,
        )
        return selected[: self.max_final]

    def select_per_topic(
        self,
        clusters: list[StoryClusterData],
        topics: list[TopicConfig],
        articles: list[PipelineArticle] | None = None,
        previous_heads: dict[str, str] | None = None,
    ) -> list[StoryClusterData]:
        """Best cluster per configured topic — backbone of the daily brief."""
        prev = previous_heads or {}
        ranked = sorted(clusters, key=self._cluster_score, reverse=True)
        by_topic: dict[str, list[StoryClusterData]] = {}
        for cluster in ranked:
            by_topic.setdefault(cluster.topic, []).append(cluster)

        articles_by_topic: dict[str, list[PipelineArticle]] = {}
        if articles:
            for article in sorted(articles, key=lambda a: a.importance_score, reverse=True):
                articles_by_topic.setdefault(article.topic, []).append(article)

        leaders: list[StoryClusterData] = []
        for topic in sorted(topics, key=lambda t: t.priority, reverse=True):
            leader: StoryClusterData | None = None
            topic_key = topic.name.lower()
            rotate = topic_key in _ROTATE_TOPICS
            prev_headline = prev.get(topic.name)

            cluster_candidates = sorted(
                by_topic.get(topic.name, []),
                key=_leader_rank_key,
                reverse=True,
            )
            for cluster in cluster_candidates:
                rep = cluster.representative or cluster.articles[0]
                if not topic_fits(rep, topic):
                    continue
                if rotate and _headline_repeated(cluster.cluster_title, prev_headline):
                    continue
                # Finance/Markets: prefer stories from the last 24h when available.
                if topic_key in _FRESH_TOPICS:
                    hours = _hours_since(rep.published_at)
                    if hours is not None and hours > 36:
                        continue
                leader = cluster
                break

            if not leader:
                for cluster in cluster_candidates:
                    rep = cluster.representative or cluster.articles[0]
                    if topic_fits(rep, topic):
                        leader = cluster
                        break

            if not leader:
                article_candidates = articles_by_topic.get(topic.name, [])
                if topic_key in _FRESH_TOPICS:
                    article_candidates = sorted(
                        article_candidates,
                        key=lambda a: (
                            a.importance_score,
                            -(_hours_since(a.published_at) or 999),
                        ),
                        reverse=True,
                    )
                for article in article_candidates:
                    if not topic_fits(article, topic):
                        continue
                    if rotate and _headline_repeated(article.title, prev_headline):
                        continue
                    leader = _singleton_cluster(article)
                    break

            if not leader and topic.name.lower() == "us news":
                for article in articles_by_topic.get(topic.name, []):
                    if _topic_match(article, topic) >= 0:
                        leader = _singleton_cluster(article)
                        break

            if leader:
                leaders.append(leader)
                if rotate and prev_headline and _headline_repeated(
                    leader.cluster_title, prev_headline
                ):
                    logger.info(
                        "Topic %s: no fresh alternative to previous headline",
                        topic.name,
                    )

        logger.info(
            "Per-topic leaders: %d / %d topics have a story",
            len(leaders),
            len(topics),
        )
        return leaders
