"""Drop articles that are not in an allowed language (default: English)."""

from __future__ import annotations

import logging
import re
from typing import Sequence

from langdetect import LangDetectException, detect_langs

from app.services.pipeline.article import PipelineArticle

logger = logging.getLogger(__name__)

_ASCII_WORD = re.compile(r"[a-zA-Z]{2,}")
_NON_BASIC_ASCII = re.compile(r"[^\x00-\x7F]")
# Common function words in Dutch, German, French, Spanish (not bare "en" — too noisy).
_FOREIGN_MARKERS = re.compile(
    r"\b("
    r"de|het|een|van|voor|tussen|naar|niet|ook|"
    r"und|der|die|das|mit|nicht|über|"
    r"les|des|une|pour|avec|"
    r"los|las|del|para"
    r")\b",
    re.IGNORECASE,
)


def _sample_text(article: PipelineArticle) -> str:
    parts = [article.title or ""]
    if article.description:
        parts.append(article.description)
    return " ".join(parts).strip()


def _has_non_latin_script(text: str) -> bool:
    """True for CJK, Cyrillic, Arabic, etc. Latin extended (café) is allowed."""
    for ch in text:
        if ch.isascii():
            continue
        if ord(ch) <= 0x024F:
            continue
        return True
    return False


def _mostly_ascii_english(text: str) -> bool:
    words = _ASCII_WORD.findall(text)
    if not words:
        return False
    return all(word.isascii() for word in words)


def _langdetect_allowed(text: str, allowed: set[str]) -> bool:
    try:
        guesses = detect_langs(text)
    except LangDetectException:
        return False
    if not guesses:
        return False
    top = guesses[0]
    return top.lang in allowed and top.prob >= 0.7


def is_allowed_language(text: str, allowed: Sequence[str] = ("en",)) -> bool:
    text = text.strip()
    if not text:
        return False

    allowed_set = {lang.lower() for lang in allowed}

    if _has_non_latin_script(text):
        return False
    if _FOREIGN_MARKERS.search(text):
        return False
    if _mostly_ascii_english(text):
        if _NON_BASIC_ASCII.search(text):
            return _langdetect_allowed(text, allowed_set)
        return True

    return _langdetect_allowed(text, allowed_set)


def filter_by_language(
    articles: list[PipelineArticle],
    allowed: Sequence[str] = ("en",),
    enabled: bool = True,
) -> list[PipelineArticle]:
    if not enabled:
        return articles

    kept: list[PipelineArticle] = []
    dropped = 0
    for article in articles:
        if is_allowed_language(_sample_text(article), allowed):
            kept.append(article)
        else:
            dropped += 1

    if dropped:
        logger.info(
            "Language filter removed %d non-English articles (allowed: %s)",
            dropped,
            ", ".join(allowed),
        )
    return kept
