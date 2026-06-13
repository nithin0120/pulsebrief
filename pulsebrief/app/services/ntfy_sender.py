"""Deliver digests via ntfy.sh push notifications (free, no account)."""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.models import Article
from app.services.twilio_sender import TwilioSender, _chunk_message

logger = logging.getLogger(__name__)

# ntfy.sh caps message bodies; keep chunks well under the limit.
MAX_NTFY_LEN = 3500


def _ascii_header(value: str) -> str:
    """ntfy headers must be latin-1 safe; strip anything that isn't."""
    return value.encode("ascii", "ignore").decode("ascii").strip() or "PulseBrief"


class NtfySender(TwilioSender):
    """Reuses TwilioSender's plain-text formatting; publishes to an ntfy topic."""

    def __init__(self) -> None:  # noqa: D107 - intentionally skips Twilio client init
        self._client = None
        self._topic = settings.ntfy_topic
        self._server = settings.ntfy_server
        self._token = settings.ntfy_token

    @property
    def is_configured(self) -> bool:
        return bool(self._topic)

    def _publish(
        self,
        body: str,
        title: str = "PulseBrief",
        click: str | None = None,
        priority: int | None = None,
        actions: str | None = None,
    ) -> bool:
        topic = self._topic
        if not topic:
            logger.warning("ntfy topic not configured; message not sent:\n%s", body[:500])
            return False

        url = f"{self._server}/{topic}"
        base_headers = {"Title": _ascii_header(title), "Tags": "newspaper"}
        if click:
            base_headers["Click"] = click
        if priority:
            base_headers["Priority"] = str(priority)
        if actions:
            base_headers["Actions"] = _ascii_header(actions)
        if self._token:
            base_headers["Authorization"] = f"Bearer {self._token}"

        chunks = _chunk_message(body, MAX_NTFY_LEN)
        all_sent = True
        with httpx.Client(timeout=30.0) as client:
            for i, chunk in enumerate(chunks):
                try:
                    resp = client.post(
                        url, content=chunk.encode("utf-8"), headers=base_headers
                    )
                    resp.raise_for_status()
                    logger.info(
                        "Published ntfy '%s' %d/%d to %s", title, i + 1, len(chunks), topic
                    )
                except Exception:
                    logger.exception("Failed to publish ntfy message chunk %d", i + 1)
                    all_sent = False
        return all_sent

    def send_message(self, text: str, to: str | None = None) -> bool:
        return self._publish(text, title="PulseBrief")

    @staticmethod
    def _priority_for(articles: list[Article]) -> int:
        """Map the most important story in the batch to an ntfy priority (1-5)."""
        top = max((a.importance or 0 for a in articles), default=0)
        if top >= 9:
            return 5  # max
        if top >= 8:
            return 4  # high
        return 3  # default

    def send_topic_digest(self, topic: str, articles: list[Article]) -> bool:
        body = self.format_topic_articles(articles)
        lead = articles[0] if articles else None
        click = lead.url if lead else None
        # ntfy view-action button. (Save/Ignore would need a phone-reachable
        # endpoint, so they are handled via the CLI instead.)
        actions = f"view, Open Article, {lead.url}" if lead else None
        return self._publish(
            body,
            title=topic,
            click=click,
            priority=self._priority_for(articles),
            actions=actions,
        )
