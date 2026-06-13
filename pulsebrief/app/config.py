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
PREFERENCES_FILE = PROJECT_ROOT / "preferences.yaml"
ENV_FILE = PROJECT_ROOT / ".env"
DB_PATH = PROJECT_ROOT / "pulsebrief.db"


@dataclass
class Settings:
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    groq_model: str = "llama-3.1-8b-instant"
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
        keywords = entry.get("keywords", [])
        queries = entry.get("queries") or keywords
        if name and queries:
            topics.append(TopicConfig(name=name, keywords=keywords, queries=queries))
    logger.info("Loaded %d topics from %s", len(topics), topics_path)
    return topics


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
