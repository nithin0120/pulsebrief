"""Group related articles into story clusters without extra LLM calls.

Clustering reuses signals we already have (title fingerprints, title
similarity, and the key_entities the summarizer extracted), so it costs no
additional tokens.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from app.services.news_fetcher import RawArticle, normalize_title, text_similarity
from app.services.ranker import _story_fingerprint
from app.services.summarizer import ArticleSummary

logger = logging.getLogger(__name__)

Item = tuple[RawArticle, float, ArticleSummary]


@dataclass
class Cluster:
    key: str
    title: str
    topic: str
    importance: int
    members: list[Item] = field(default_factory=list)

    @property
    def lead(self) -> Item:
        return max(self.members, key=lambda m: (m[2].importance or 0, m[1]))

    @property
    def summary(self) -> str:
        return self.lead[2].long_summary or self.lead[2].tldr

    @property
    def what_happened_today(self) -> str | None:
        return self.lead[2].what_changed_today or self.lead[2].tldr

    @property
    def why_it_matters(self) -> str | None:
        return self.lead[2].why_it_matters

    @property
    def source_links(self) -> list[dict[str, str]]:
        seen: set[str] = set()
        links: list[dict[str, str]] = []
        for raw, _score, _summary in sorted(
            self.members, key=lambda m: (m[2].importance or 0, m[1]), reverse=True
        ):
            if raw.source.lower() in seen:
                continue
            seen.add(raw.source.lower())
            links.append({"source": raw.source, "url": raw.url})
        return links[:4]

    @property
    def conflicting_details(self) -> str | None:
        sources = {raw.source for raw, _, _ in self.members}
        if len(sources) >= 2:
            return f"Reported by {len(sources)} outlets — compare framing across sources."
        return None


def _entities(summary: ArticleSummary) -> set[str]:
    return {e.lower() for e in (summary.key_entities or []) if len(e) > 2}


def _related(a: Item, b: Item) -> bool:
    ra, _, sa = a
    rb, _, sb = b
    if _story_fingerprint(ra.title) == _story_fingerprint(rb.title):
        return True
    if text_similarity(normalize_title(ra.title), normalize_title(rb.title)) >= 0.6:
        return True
    return len(_entities(sa) & _entities(sb)) >= 2


def cluster_items(items: list[Item]) -> list[Cluster]:
    """Union-find clustering over related items."""
    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    for i in range(n):
        for j in range(i + 1, n):
            if _related(items[i], items[j]):
                union(i, j)

    groups: dict[int, list[Item]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(items[idx])

    clusters: list[Cluster] = []
    for members in groups.values():
        lead_raw, lead_score, lead_summary = max(
            members, key=lambda m: (m[2].importance or 0, m[1])
        )
        key = hashlib.sha1(
            _story_fingerprint(lead_raw.title).encode("utf-8")
        ).hexdigest()[:16]
        clusters.append(
            Cluster(
                key=key,
                title=lead_raw.title,
                topic=lead_summary.category or lead_raw.topic,
                importance=lead_summary.importance or 0,
                members=members,
            )
        )

    clusters.sort(key=lambda c: c.importance, reverse=True)
    logger.info("Clustered %d articles into %d stories", n, len(clusters))
    return clusters
