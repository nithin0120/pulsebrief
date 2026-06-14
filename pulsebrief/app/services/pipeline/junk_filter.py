"""Drop obvious junk before scoring."""

from __future__ import annotations

import logging
import re

from app.services.pipeline.article import PipelineArticle

logger = logging.getLogger(__name__)

_JUNK_TITLE = re.compile(
    r"^(show hn:|ask hn:|tell hn:)?\s*[\w.-]+\s+v?\d+\.\d+",
    re.IGNORECASE,
)
_LOCAL_NOISE = re.compile(
    r"valedictorian|salutatorian|obituary|horoscope|lottery numbers",
    re.IGNORECASE,
)


def filter_junk(articles: list[PipelineArticle]) -> list[PipelineArticle]:
    kept: list[PipelineArticle] = []
    for a in articles:
        title = (a.title or "").strip()
        if len(title) < 12:
            continue
        if _JUNK_TITLE.match(title):
            continue
        if _LOCAL_NOISE.search(title):
            continue
        if a.source_type == "hn" and title.lower().startswith(("show hn", "ask hn")):
            # Keep Show HN for tech signal but drop Ask HN noise
            if title.lower().startswith("ask hn"):
                continue
        kept.append(a)
    dropped = len(articles) - len(kept)
    if dropped:
        logger.info("Junk filter removed %d articles", dropped)
    return kept
