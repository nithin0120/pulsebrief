"""Delivery channel factory and console fallback sender."""

from __future__ import annotations

import logging

from app.config import settings
from app.services.twilio_sender import TwilioSender

logger = logging.getLogger(__name__)


class ConsoleSender(TwilioSender):
    """Prints messages to stdout. Reuses plain-text formatting from TwilioSender."""

    def __init__(self) -> None:  # noqa: D107 - intentionally skips Twilio client init
        self._client = None

    @property
    def is_configured(self) -> bool:
        return True

    def send_message(self, text: str, to: str | None = None) -> bool:
        print("\n--- PulseBrief (console) ---\n")
        print(text)
        return True


def get_sender():
    """Return the sender for the configured DELIVERY_CHANNEL."""
    channel = settings.delivery_channel
    if channel == "ntfy":
        from app.services.ntfy_sender import NtfySender

        return NtfySender()
    if channel == "slack":
        from app.services.slack_sender import SlackSender

        return SlackSender()
    if channel == "console":
        return ConsoleSender()
    if channel != "twilio":
        logger.warning("Unknown DELIVERY_CHANNEL '%s'; defaulting to twilio", channel)
    return TwilioSender()
