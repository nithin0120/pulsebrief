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


settings = Settings.from_env()
