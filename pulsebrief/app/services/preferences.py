"""Portable user preferences (mutes, disliked sources, topic edits).

Stored in ``preferences.yaml`` / ``topics.yaml`` rather than SQLite so they are
git-committable and therefore honored by the scheduled GitHub Actions run, which
executes on an ephemeral runner without the local database.
"""

from __future__ import annotations

import logging

import yaml

from app.config import PREFERENCES_FILE, TOPICS_FILE, TopicConfig

logger = logging.getLogger(__name__)

_DEFAULT_PREFS: dict[str, list[str]] = {
    "muted_keywords": [],
    "muted_sources": [],
    "disliked_sources": [],
    "preferred_topics": [],
}


def load_preferences() -> dict[str, list[str]]:
    if not PREFERENCES_FILE.exists():
        return {k: list(v) for k, v in _DEFAULT_PREFS.items()}
    with open(PREFERENCES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    prefs = {k: list(v) for k, v in _DEFAULT_PREFS.items()}
    for key in prefs:
        value = data.get(key)
        if isinstance(value, list):
            prefs[key] = [str(x).strip() for x in value if str(x).strip()]
    return prefs


def _save_preferences(prefs: dict[str, list[str]]) -> None:
    with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(prefs, f, sort_keys=True, allow_unicode=True)


def _add(key: str, value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    prefs = load_preferences()
    lowered = {v.lower() for v in prefs[key]}
    if value.lower() in lowered:
        return False
    prefs[key].append(value)
    _save_preferences(prefs)
    logger.info("Added %s to %s", value, key)
    return True


def _remove(key: str, value: str) -> bool:
    value = value.strip().lower()
    prefs = load_preferences()
    new_list = [v for v in prefs[key] if v.lower() != value]
    if len(new_list) == len(prefs[key]):
        return False
    prefs[key] = new_list
    _save_preferences(prefs)
    return True


def mute_keyword(keyword: str) -> bool:
    return _add("muted_keywords", keyword)


def mute_source(source: str) -> bool:
    return _add("muted_sources", source)


def dislike_source(source: str) -> bool:
    return _add("disliked_sources", source)


class PreferenceFilter:
    """Fast in-memory checks built from the current preferences."""

    def __init__(self) -> None:
        prefs = load_preferences()
        self.muted_keywords = [k.lower() for k in prefs["muted_keywords"]]
        self.muted_sources = [s.lower() for s in prefs["muted_sources"]]
        self.disliked_sources = [s.lower() for s in prefs["disliked_sources"]]
        self.preferred_topics = [t.lower() for t in prefs["preferred_topics"]]

    def is_muted(self, title: str, description: str | None, source: str) -> bool:
        src = source.lower()
        if any(muted in src for muted in self.muted_sources):
            return True
        text = f"{title} {description or ''}".lower()
        return any(kw in text for kw in self.muted_keywords)

    def source_penalty(self, source: str) -> float:
        return 0.6 if any(d in source.lower() for d in self.disliked_sources) else 1.0

    def topic_boost(self, topic: str) -> float:
        return 1.15 if topic.lower() in self.preferred_topics else 1.0


# --- Topic management (edits topics.yaml) -------------------------------------


def _slugify_keywords(name: str) -> list[str]:
    return [name.strip()]


def add_topic(name: str, keywords: list[str] | None = None) -> bool:
    name = name.strip()
    if not name:
        return False
    raw = {"topics": []}
    if TOPICS_FILE.exists():
        with open(TOPICS_FILE, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {"topics": []}
    topics = raw.setdefault("topics", [])
    if any(str(t.get("name", "")).lower() == name.lower() for t in topics):
        return False
    kws = keywords or _slugify_keywords(name)
    topics.append({"name": name, "keywords": kws, "queries": kws})
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, sort_keys=False, allow_unicode=True)
    logger.info("Added topic '%s'", name)
    return True


def remove_topic(name: str) -> bool:
    name = name.strip().lower()
    if not TOPICS_FILE.exists():
        return False
    with open(TOPICS_FILE, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {"topics": []}
    topics = raw.get("topics", [])
    new_topics = [t for t in topics if str(t.get("name", "")).lower() != name]
    if len(new_topics) == len(topics):
        return False
    raw["topics"] = new_topics
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, sort_keys=False, allow_unicode=True)
    return True
