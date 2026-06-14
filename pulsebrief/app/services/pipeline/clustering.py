"""Stage 5: TF-IDF story clustering with rapidfuzz fallback."""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict

from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config import load_config
from app.services.pipeline.article import PipelineArticle, StoryClusterData

logger = logging.getLogger(__name__)


def _doc_text(article: PipelineArticle) -> str:
    return f"{article.title} {article.description or ''}"


def _cluster_key(title: str) -> str:
    return hashlib.sha1(title.lower().encode()).hexdigest()[:16]


class StoryClusterer:
    def __init__(self, config: dict | None = None) -> None:
        cfg = (config or load_config()).get("clustering", {})
        self.similarity_threshold = float(cfg.get("similarity_threshold", 0.22))
        self.min_df = int(cfg.get("min_df", 1))
        self.max_features = int(cfg.get("max_features", 4000))
        self.fuzzy_fallback = int(cfg.get("fuzzy_fallback_threshold", 80))

    def cluster(self, articles: list[PipelineArticle]) -> list[StoryClusterData]:
        if not articles:
            return []
        # Cluster within each topic so stories don't steal slots from other categories.
        by_topic: dict[str, list[PipelineArticle]] = {}
        for article in articles:
            by_topic.setdefault(article.topic, []).append(article)

        clusters: list[StoryClusterData] = []
        for group in by_topic.values():
            clusters.extend(self._cluster_group(group))

        clusters.sort(key=lambda c: c.importance_score, reverse=True)
        logger.info(
            "Clustered %d articles into %d story clusters (%d topics)",
            len(articles),
            len(clusters),
            len(by_topic),
        )
        return clusters

    def _cluster_group(self, articles: list[PipelineArticle]) -> list[StoryClusterData]:
        if not articles:
            return []
        if len(articles) == 1:
            a = articles[0]
            return [
                StoryClusterData(
                    cluster_id=_cluster_key(a.title),
                    cluster_title=a.title,
                    topic=a.topic,
                    articles=[a],
                    importance_score=a.importance_score,
                    is_opinion=a.is_opinion,
                )
            ]

        texts = [_doc_text(a) for a in articles]
        try:
            vectorizer = TfidfVectorizer(
                min_df=self.min_df,
                max_features=self.max_features,
                stop_words="english",
                ngram_range=(1, 2),
            )
            matrix = vectorizer.fit_transform(texts)
            sim = cosine_similarity(matrix)
        except ValueError:
            sim = None

        parent = list(range(len(articles)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            parent[find(i)] = find(j)

        for i in range(len(articles)):
            for j in range(i + 1, len(articles)):
                related = False
                if sim is not None and sim[i, j] >= self.similarity_threshold:
                    related = True
                elif fuzz.token_set_ratio(
                    articles[i].title_normalized, articles[j].title_normalized
                ) >= self.fuzzy_fallback:
                    related = True
                if related:
                    union(i, j)

        groups: dict[int, list[PipelineArticle]] = defaultdict(list)
        for idx, article in enumerate(articles):
            groups[find(idx)].append(article)

        clusters: list[StoryClusterData] = []
        for members in groups.values():
            rep = max(members, key=lambda a: (a.importance_score, a.reputation))
            imp = sum(a.importance_score for a in members) / len(members)
            imp += min(3.0, (len({a.source for a in members}) - 1) * 0.8)
            diversity = min(1.0, len({a.source_domain for a in members}) / 4.0)
            clusters.append(
                StoryClusterData(
                    cluster_id=_cluster_key(rep.title),
                    cluster_title=rep.title,
                    topic=rep.topic,
                    articles=members,
                    importance_score=round(imp, 3),
                    source_diversity_score=diversity,
                    is_opinion=rep.is_opinion,
                    is_breaking=imp >= 8.0 and len(members) >= 2,
                )
            )

        clusters.sort(key=lambda c: c.importance_score, reverse=True)
        return clusters
