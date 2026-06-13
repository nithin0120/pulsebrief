"""Send digest messages and handle replies via Twilio (SMS or WhatsApp)."""

from __future__ import annotations

import json
import logging
import re
import time

from app.config import settings
from app.models import Article

logger = logging.getLogger(__name__)


def _decode_entities(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []

# Keep each outbound message comfortably under Twilio's 1600-char body limit.
MAX_MESSAGE_LEN = 1400


class TwilioSender:
    def __init__(self) -> None:
        self._client = None
        if settings.twilio_account_sid and settings.twilio_auth_token:
            try:
                from twilio.rest import Client

                self._client = Client(
                    settings.twilio_account_sid, settings.twilio_auth_token
                )
            except Exception:
                logger.exception("Failed to initialize Twilio client")

    @property
    def is_configured(self) -> bool:
        return (
            self._client is not None
            and bool(settings.twilio_from_number)
            and bool(settings.twilio_to_number)
        )

    def send_message(self, text: str, to: str | None = None) -> bool:
        if not self._client:
            logger.warning("Twilio not configured; message not sent:\n%s", text[:500])
            return False

        from_number = settings.twilio_from_number
        target = to or settings.twilio_to_number
        if not from_number or not target:
            logger.warning("Twilio from/to number not configured")
            return False

        chunks = _chunk_message(text, MAX_MESSAGE_LEN)
        all_sent = True
        for i, chunk in enumerate(chunks):
            try:
                self._client.messages.create(
                    body=chunk,
                    from_=from_number,
                    to=target,
                )
                logger.info(
                    "Sent Twilio message %d/%d to %s", i + 1, len(chunks), target
                )
            except Exception:
                logger.exception("Failed to send Twilio message chunk %d", i + 1)
                all_sent = False
            if i < len(chunks) - 1:
                time.sleep(1.0)
        return all_sent

    @staticmethod
    def _label(article: Article) -> str:
        return " [Opinion/Analysis]" if getattr(article, "is_opinion", False) else ""

    def format_digest(self, articles: list[Article]) -> str:
        lines = ["Good morning — here's your PulseBrief.", ""]
        for article in articles:
            pos = article.digest_position or 0
            lines.append(f"{pos}. [{article.topic}] {article.title}{self._label(article)}")
            lines.append(f"   Source: {article.source}")
            lines.append(f"   TLDR: {article.tldr or 'N/A'}")
            lines.append(f"   Why it matters: {article.why_it_matters or 'N/A'}")
            lines.append(f"   Link: {article.url}")
            lines.append("")

        lines.append("Reply with: more 1 | more 2 | full 1 | topics")
        return "\n".join(lines)

    def format_topic_articles(self, articles: list[Article]) -> str:
        """Body for a single topic's notification (no topic tag — that's the title)."""
        lines: list[str] = []
        for i, article in enumerate(articles, 1):
            pos = article.digest_position or i
            imp = f" ·{article.importance}/10" if getattr(article, "importance", None) else ""
            lines.append(f"{pos}. {article.title}{self._label(article)}{imp}")
            lines.append(f"   Source: {article.source}")
            lines.append(f"   TLDR: {article.tldr or 'N/A'}")
            if article.why_it_matters:
                lines.append(f"   Why it matters: {article.why_it_matters}")
            lines.append(f"   Link: {article.url}")
            lines.append("")
        return "\n".join(lines).strip()

    def send_topic_digest(self, topic: str, articles: list[Article]) -> bool:
        """Send one notification for a topic. Title is embedded for text channels."""
        body = self.format_topic_articles(articles)
        return self.send_message(f"[{topic}]\n\n{body}")

    def format_more(self, article: Article) -> str:
        parts = [
            f"More on #{article.digest_position}: {article.title}",
            f"Source: {article.source}",
            "",
            article.long_summary or article.tldr or "No extended summary available.",
        ]
        if article.what_changed_today:
            parts.extend(["", f"What changed today: {article.what_changed_today}"])
        if article.what_to_watch_next:
            parts.extend(["", f"What to watch next: {article.what_to_watch_next}"])
        if article.follow_up_question:
            parts.extend(["", f"Worth asking: {article.follow_up_question}"])
        parts.extend(["", f"Link: {article.url}"])
        return "\n".join(parts)

    def format_full(self, article: Article) -> str:
        parts = [
            f"Full brief #{article.digest_position}: {article.title}{self._label(article)}",
            f"Source: {article.source}",
            f"Topic: {article.topic}",
            f"URL: {article.url}",
            "",
            article.long_summary or article.tldr or "No summary available.",
        ]
        if article.background:
            parts.extend(["", f"Background: {article.background}"])
        if article.why_it_matters:
            parts.extend(["", f"Why it matters: {article.why_it_matters}"])
        if article.bias_or_angle:
            parts.extend(["", f"Bias/angle: {article.bias_or_angle}"])
        entities = _decode_entities(article.key_entities)
        if entities:
            parts.extend(["", f"Key entities: {', '.join(entities)}"])
        if article.what_to_watch_next:
            parts.extend(["", f"What to watch next: {article.what_to_watch_next}"])
        if article.description:
            parts.extend(["", f"Original excerpt: {article.description}"])
        return "\n".join(parts)

    def format_topics(self, topics: list[str]) -> str:
        lines = ["Active PulseBrief topics:"]
        for i, topic in enumerate(topics, 1):
            lines.append(f"{i}. {topic}")
        return "\n".join(lines)


def _chunk_message(text: str, max_len: int) -> list[str]:
    """Split text into chunks <= max_len, preferring article (blank-line) boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # A single block larger than max_len must be hard-split.
        while len(block) > max_len:
            chunks.append(block[:max_len])
            block = block[max_len:]
        current = block
    if current:
        chunks.append(current)
    return chunks


def parse_command(text: str) -> tuple[str, int | None]:
    """Parse commands like 'more 1', 'full 2', 'explain 3', 'topics', 'today'."""
    text = text.strip().lower()
    if text in ("topics", "today", "history", "run digest", "run-digest"):
        return text.replace("-", " "), None

    match = re.match(r"^(more|full|explain|save|ignore)\s+(\d+)$", text)
    if match:
        return match.group(1), int(match.group(2))
    return text, None
