"""Stage 6: rank and select top story clusters for the final brief."""

from __future__ import annotations

import logging

from app.config import TopicConfig, load_config
from app.services.pipeline.article import StoryClusterData

logger = logging.getLogger(__name__)

INTERNATIONAL_TOPICS = {"world news", "geopolitics", "international"}


class ClusterRanker:
    def __init__(self, config: dict | None = None) -> None:
        cfg = (config or load_config()).get("ranking", {})
        self.max_final = int(cfg.get("max_final_clusters", 8))
        self.min_final = int(cfg.get("min_final_clusters", 3))
        self.max_per_topic = int(cfg.get("max_clusters_per_topic", 2))
        self.max_per_source = int(cfg.get("max_articles_per_source", 2))
        self.require_international = bool(cfg.get("require_international", True))

    def select(
        self,
        clusters: list[StoryClusterData],
        topics: list[TopicConfig],
    ) -> list[StoryClusterData]:
        topic_caps = {t.name: t.max_clusters for t in topics}
        topic_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        selected: list[StoryClusterData] = []

        ranked = sorted(
            clusters,
            key=lambda c: (
                c.importance_score,
                c.source_count,
                c.source_diversity_score,
            ),
            reverse=True,
        )

        for cluster in ranked:
            cap = topic_caps.get(cluster.topic, self.max_per_topic)
            if topic_counts.get(cluster.topic, 0) >= cap and not cluster.is_breaking:
                continue
            # Source diversity: skip if we'd overload one outlet
            overloaded = False
            for src in cluster.source_names:
                if source_counts.get(src.lower(), 0) >= self.max_per_source:
                    overloaded = True
                    break
            if overloaded and not cluster.is_breaking:
                continue

            selected.append(cluster)
            topic_counts[cluster.topic] = topic_counts.get(cluster.topic, 0) + 1
            for src in cluster.source_names:
                source_counts[src.lower()] = source_counts.get(src.lower(), 0) + 1
            if len(selected) >= self.max_final:
                break

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

        selected.sort(key=lambda c: c.importance_score, reverse=True)
        logger.info("Selected %d finalist clusters from %d", len(selected), len(clusters))
        return selected[: self.max_final]
