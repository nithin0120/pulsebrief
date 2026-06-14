"""Stage 6: rank and select top story clusters for the final brief."""

from __future__ import annotations

import hashlib
import logging

from app.config import TopicConfig, load_config
from app.services.pipeline.article import PipelineArticle, StoryClusterData
from app.services.pipeline.scorer import topic_fits, _topic_match

logger = logging.getLogger(__name__)

INTERNATIONAL_TOPICS = {"world news", "geopolitics", "international"}


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
    ) -> list[StoryClusterData]:
        """Best cluster per configured topic — backbone of the daily brief."""
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
            for cluster in by_topic.get(topic.name, []):
                rep = cluster.representative or cluster.articles[0]
                if topic_fits(rep, topic):
                    leader = cluster
                    break
            if not leader:
                for article in articles_by_topic.get(topic.name, []):
                    if topic_fits(article, topic):
                        leader = _singleton_cluster(article)
                        break
            # US: if nothing domestic enough, still show top NPR story that isn't UK-tagged junk.
            if not leader and topic.name.lower() == "us news":
                for article in articles_by_topic.get(topic.name, []):
                    if _topic_match(article, topic) >= 0:
                        leader = _singleton_cluster(article)
                        break
            if leader:
                leaders.append(leader)

        logger.info(
            "Per-topic leaders: %d / %d topics have a story",
            len(leaders),
            len(topics),
        )
        return leaders
