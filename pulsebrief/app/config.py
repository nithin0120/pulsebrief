"""Application configuration loaded from environment and topics.yaml."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOPICS_FILE = PROJECT_ROOT / "topics.yaml"
SOURCES_FILE = PROJECT_ROOT / "sources.yaml"
CONFIG_FILE = PROJECT_ROOT / "config.yaml"
PREFERENCES_FILE = PROJECT_ROOT / "preferences.yaml"
ENV_FILE = PROJECT_ROOT / ".env"
DB_PATH = PROJECT_ROOT / "pulsebrief.db"


@dataclass
class Settings:
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    groq_model: str = "llama-3.1-8b-instant"
    groq_deep_model: str = "llama-3.3-70b-versatile"
    groq_max_daily_requests: int = 20
    groq_max_tokens_per_digest: int = 6000
    news_api_key: str | None = None
    delivery_channel: str = "twilio"
    slack_bot_token: str | None = None
    slack_channel_id: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    twilio_to_number: str | None = None
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"
    ntfy_token: str | None = None
    digest_time: str = "08:00"
    digest_interval_hours: int = 6
    run_on_startup: bool = True
    timezone: str = "America/Los_Angeles"
    max_articles_per_topic: int = 3
    max_total_articles: int = 40
    candidates_per_topic: int = 6
    max_per_source: int = 2
    min_importance: int = 6
    database_url: str = f"sqlite:///{DB_PATH}"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(ENV_FILE)
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            groq_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            groq_deep_model=os.getenv("GROQ_DEEP_MODEL", "llama-3.3-70b-versatile"),
            groq_max_daily_requests=int(os.getenv("GROQ_MAX_DAILY_REQUESTS", "20")),
            groq_max_tokens_per_digest=int(os.getenv("GROQ_MAX_TOKENS_PER_DIGEST", "6000")),
            news_api_key=os.getenv("NEWS_API_KEY") or None,
            delivery_channel=(os.getenv("DELIVERY_CHANNEL") or "twilio").strip().lower(),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN") or None,
            slack_channel_id=os.getenv("SLACK_CHANNEL_ID") or None,
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID") or None,
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN") or None,
            twilio_from_number=os.getenv("TWILIO_FROM_NUMBER") or None,
            twilio_to_number=os.getenv("TWILIO_TO_NUMBER") or None,
            ntfy_topic=os.getenv("NTFY_TOPIC") or None,
            ntfy_server=(os.getenv("NTFY_SERVER") or "https://ntfy.sh").rstrip("/"),
            ntfy_token=os.getenv("NTFY_TOKEN") or None,
            digest_time=os.getenv("DIGEST_TIME", "08:00"),
            digest_interval_hours=int(os.getenv("DIGEST_INTERVAL_HOURS", "6")),
            run_on_startup=(os.getenv("RUN_ON_STARTUP", "true").strip().lower() == "true"),
            timezone=os.getenv("TIMEZONE", "America/Los_Angeles"),
            max_articles_per_topic=int(os.getenv("MAX_ARTICLES_PER_TOPIC", "3")),
            max_total_articles=int(os.getenv("MAX_TOTAL_ARTICLES", "40")),
            candidates_per_topic=int(os.getenv("CANDIDATES_PER_TOPIC", "6")),
            max_per_source=int(os.getenv("MAX_PER_SOURCE", "2")),
            min_importance=int(os.getenv("MIN_IMPORTANCE", "6")),
        )


@dataclass
class TopicConfig:
    name: str
    keywords: list[str]
    queries: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    priority: int = 3
    max_clusters: int = 2
    source_preferences: list[str] = field(default_factory=list)


@dataclass
class SourceConfig:
    name: str
    type: str  # newsapi | gdelt | rss | hn
    category: str = "mixed"
    url: str | None = None
    domain: str | None = None
    reputation: float = 0.6
    perspective: str | None = None
    enabled: bool = True


def load_topics(path: Path | None = None) -> list[TopicConfig]:
    """Load topic definitions from topics.yaml."""
    topics_path = path or TOPICS_FILE
    if not topics_path.exists():
        logger.warning("Topics file not found at %s", topics_path)
        return []

    with open(topics_path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    topics: list[TopicConfig] = []
    for entry in raw.get("topics", []):
        name = entry.get("name", "")
        keywords = entry.get("keywords", []) or []
        queries = entry.get("queries") or keywords
        if name and queries:
            topics.append(
                TopicConfig(
                    name=name,
                    keywords=keywords,
                    queries=queries,
                    negative_keywords=entry.get("negative_keywords", []) or [],
                    priority=int(entry.get("priority", 3)),
                    max_clusters=int(entry.get("max_clusters", 2)),
                    source_preferences=entry.get("source_preferences", []) or [],
                )
            )
    logger.info("Loaded %d topics from %s", len(topics), topics_path)
    return topics


def load_sources(path: Path | None = None) -> list[SourceConfig]:
    """Load source connector definitions from sources.yaml."""
    sources_path = path or SOURCES_FILE
    if not sources_path.exists():
        logger.warning("Sources file not found at %s; using NewsAPI+GDELT only", sources_path)
        return [
            SourceConfig(name="NewsAPI", type="newsapi", reputation=0.7),
            SourceConfig(name="GDELT", type="gdelt", reputation=0.6),
        ]

    with open(sources_path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    sources: list[SourceConfig] = []
    for entry in raw.get("sources", []):
        if not entry.get("enabled", True):
            continue
        name, stype = entry.get("name"), entry.get("type")
        if not name or not stype:
            continue
        sources.append(
            SourceConfig(
                name=name,
                type=stype,
                category=entry.get("category", "mixed"),
                url=entry.get("url"),
                domain=entry.get("domain"),
                reputation=float(entry.get("reputation", 0.6)),
                perspective=entry.get("perspective"),
                enabled=True,
            )
        )
    logger.info("Loaded %d enabled sources from %s", len(sources), sources_path)
    return sources


_DEFAULT_CONFIG: dict[str, Any] = {
    "fetch": {
        "max_articles_per_source": 30,
        "max_total_articles": 300,
        "newsapi_page_size": 30,
        "refetch_skip_hours": 6,
    },
    "dedup": {"fuzzy_title_threshold": 88, "description_threshold": 90},
    "clustering": {
        "similarity_threshold": 0.22,
        "min_df": 1,
        "max_features": 4000,
        "fuzzy_fallback_threshold": 80,
    },
    "scoring": {
        "weights": {
            "topic_priority": 1.0,
            "source_reputation": 1.2,
            "recency": 1.5,
            "multi_source_bonus": 2.0,
            "keyword_importance": 1.3,
            "user_preference": 1.0,
            "international_bonus": 0.8,
            "muted_keyword_penalty": 5.0,
            "low_quality_source_penalty": 1.5,
            "opinion_penalty": 0.6,
        },
        "recency_half_life_hours": 18,
        "low_quality_sources": ["biztoc", "slashdot", "msn"],
    },
    "ranking": {
        "max_final_clusters": 8,
        "min_final_clusters": 3,
        "max_clusters_per_topic": 2,
        "max_articles_per_source": 2,
        "require_international": True,
    },
    "extraction": {
        "enabled": True,
        "max_finalist_full_text": 8,
        "extra_sources_per_cluster": 1,
        "max_chars": 6000,
        "timeout_seconds": 12,
    },
    "compression": {"key_sentences_per_cluster": 5, "max_tokens_per_cluster": 700},
    "groq": {"enabled": True, "max_daily_requests": 20, "max_tokens_per_digest": 6000},
    "ntfy": {
        "send_summary_notification": True,
        "send_per_section": True,
        "breaking_importance": 9,
        "max_top_stories_in_summary": 6,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load config.yaml merged over built-in defaults (missing keys are safe)."""
    config_path = path or CONFIG_FILE
    user: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    return _deep_merge(_DEFAULT_CONFIG, user)


def validate_settings(s: Settings | None = None) -> list[str]:
    """Return a list of human-readable configuration problems (empty = all good).

    Warnings are non-fatal: the app still runs (e.g. falls back to extractive
    summaries or console delivery), but the user should know what's degraded.
    """
    s = s or settings
    problems: list[str] = []

    if not s.news_api_key:
        problems.append(
            "NEWS_API_KEY is not set — falling back to GDELT, which is lower quality."
        )
    if not s.groq_api_key and not s.openai_api_key:
        problems.append(
            "Neither GROQ_API_KEY nor OPENAI_API_KEY is set — using extractive "
            "summaries only (no AI insight, importance, or categorization)."
        )

    channel = s.delivery_channel
    if channel == "ntfy" and not s.ntfy_topic:
        problems.append("DELIVERY_CHANNEL=ntfy but NTFY_TOPIC is empty — output will print to console.")
    elif channel == "twilio" and not (
        s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number and s.twilio_to_number
    ):
        problems.append("DELIVERY_CHANNEL=twilio but Twilio credentials are incomplete.")
    elif channel == "slack" and not (s.slack_bot_token and s.slack_channel_id):
        problems.append("DELIVERY_CHANNEL=slack but Slack credentials are incomplete.")

    if not (1 <= s.min_importance <= 10):
        problems.append(f"MIN_IMPORTANCE={s.min_importance} is outside 1-10.")

    return problems


def log_validation(s: Settings | None = None) -> None:
    for problem in validate_settings(s):
        logger.warning("Config: %s", problem)


settings = Settings.from_env()
