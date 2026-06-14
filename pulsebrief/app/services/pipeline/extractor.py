"""Stage 7: fetch full article text only for finalist clusters."""

from __future__ import annotations

import logging
import re

import httpx
import trafilatura

from app.config import load_config
from app.services.pipeline.article import PipelineArticle, StoryClusterData

logger = logging.getLogger(__name__)

USER_AGENT = "PulseBrief/1.0 (local news digest)"


def _extract_with_trafilatura(html: str, url: str) -> str | None:
    try:
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
        return text.strip() if text else None
    except Exception:
        return None


def _extract_with_readability(html: str) -> str | None:
    try:
        from readability import Document

        doc = Document(html)
        summary = doc.summary()
        text = re.sub(r"<[^>]+>", " ", summary)
        text = re.sub(r"\s+", " ", text).strip()
        return text or None
    except Exception:
        return None


def _extract_with_bs4(html: str) -> str | None:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text[:8000] if text else None
    except Exception:
        return None


class ArticleExtractor:
    def __init__(self, config: dict | None = None) -> None:
        cfg = (config or load_config()).get("extraction", {})
        self.enabled = bool(cfg.get("enabled", True))
        self.max_clusters = int(cfg.get("max_finalist_full_text", 8))
        self.extra_per_cluster = int(cfg.get("extra_sources_per_cluster", 1))
        self.max_chars = int(cfg.get("max_chars", 6000))
        self.timeout = float(cfg.get("timeout_seconds", 12))

    def _fetch_html(self, url: str) -> str | None:
        try:
            with httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            logger.debug("Fetch failed for %s: %s", url[:60], exc)
            return None

    def extract_url(self, url: str) -> str | None:
        if not self.enabled or not url:
            return None
        html = self._fetch_html(url)
        if not html:
            return None
        for fn in (_extract_with_trafilatura, _extract_with_readability, _extract_with_bs4):
            try:
                text = fn(html, url) if fn is _extract_with_trafilatura else fn(html)
            except TypeError:
                text = fn(html)
            if text and len(text) > 120:
                return text[: self.max_chars]
        return None

    def enrich_clusters(
        self, clusters: list[StoryClusterData]
    ) -> dict[str, str]:
        """Return {url: extracted_text} for representative + extra sources."""
        extracted: dict[str, str] = {}
        for cluster in clusters[: self.max_clusters]:
            urls: list[str] = []
            if cluster.representative:
                urls.append(cluster.representative.url)
            others = sorted(
                [a for a in cluster.articles if cluster.representative and a.url != cluster.representative.url],
                key=lambda a: a.importance_score,
                reverse=True,
            )
            urls.extend(a.url for a in others[: self.extra_per_cluster])
            for url in urls:
                if url in extracted:
                    continue
                text = self.extract_url(url)
                if text:
                    extracted[url] = text
                    logger.info("Extracted %d chars from %s", len(text), url[:60])
        return extracted
