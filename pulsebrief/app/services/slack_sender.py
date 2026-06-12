"""Send digest messages and handle Slack interactions."""

from __future__ import annotations

import logging
import re

from app.config import settings
from app.models import Article

logger = logging.getLogger(__name__)


class SlackSender:
    def __init__(self) -> None:
        self._client = None
        if settings.slack_bot_token:
            try:
                from slack_sdk import WebClient

                self._client = WebClient(token=settings.slack_bot_token)
            except Exception:
                logger.exception("Failed to initialize Slack client")

    @property
    def is_configured(self) -> bool:
        return self._client is not None and bool(settings.slack_channel_id)

    def send_message(self, text: str, channel: str | None = None) -> bool:
        if not self._client:
            logger.warning("Slack not configured; message not sent:\n%s", text[:500])
            return False

        target = channel or settings.slack_channel_id
        if not target:
            logger.warning("No Slack channel configured")
            return False

        try:
            self._client.chat_postMessage(channel=target, text=text, mrkdwn=False)
            logger.info("Sent Slack message to %s", target)
            return True
        except Exception:
            logger.exception("Failed to send Slack message")
            return False

    def format_digest(self, articles: list[Article]) -> str:
        lines = ["Good morning — here's your PulseBrief.", ""]
        for article in articles:
            pos = article.digest_position or 0
            lines.append(f"{pos}. [{article.topic}] {article.title}")
            lines.append(f"   Source: {article.source}")
            lines.append(f"   TLDR: {article.tldr or 'N/A'}")
            lines.append(f"   Why it matters: {article.why_it_matters or 'N/A'}")
            lines.append(f"   Link: {article.url}")
            lines.append("")

        lines.append("Reply with:")
        lines.append("more 1")
        lines.append("more 2")
        lines.append("full 1")
        return "\n".join(lines)

    def format_topic_articles(self, articles: list[Article]) -> str:
        lines: list[str] = []
        for article in articles:
            lines.append(article.title)
            lines.append(f"   Source: {article.source}")
            lines.append(f"   TLDR: {article.tldr or 'N/A'}")
            lines.append(f"   Why it matters: {article.why_it_matters or 'N/A'}")
            lines.append(f"   Link: {article.url}")
            lines.append("")
        return "\n".join(lines).strip()

    def send_topic_digest(self, topic: str, articles: list[Article]) -> bool:
        body = self.format_topic_articles(articles)
        return self.send_message(f"*[{topic}]*\n\n{body}")

    def format_more(self, article: Article) -> str:
        return (
            f"*More on #{article.digest_position}: {article.title}*\n"
            f"Source: {article.source}\n\n"
            f"{article.long_summary or article.tldr or 'No extended summary available.'}"
        )

    def format_full(self, article: Article) -> str:
        parts = [
            f"*Full brief #{article.digest_position}: {article.title}*",
            f"Source: {article.source}",
            f"Topic: {article.topic}",
            f"URL: {article.url}",
            "",
            article.long_summary or article.tldr or "No summary available.",
        ]
        if article.description:
            parts.extend(["", f"Original excerpt: {article.description}"])
        return "\n".join(parts)

    def format_topics(self, topics: list[str]) -> str:
        lines = ["*Active PulseBrief topics:*"]
        for i, topic in enumerate(topics, 1):
            lines.append(f"{i}. {topic}")
        return "\n".join(lines)


def parse_slack_command(text: str) -> tuple[str, int | None]:
    """Parse commands like 'more 1', 'full 2', 'topics', 'run digest'."""
    text = text.strip().lower()
    if text in ("topics", "run digest", "run-digest"):
        return text.replace("-", " "), None

    match = re.match(r"^(more|full)\s+(\d+)$", text)
    if match:
        return match.group(1), int(match.group(2))
    return text, None
