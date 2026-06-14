"""Deliver digests via ntfy.sh push notifications (free, no account)."""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.models import Article
from app.services.brief import (
    digest_notification_title,
    format_ntfy_by_section,
    format_ntfy_teaser,
    is_breaking_story,
)
from app.services.twilio_sender import TwilioSender, _chunk_message

logger = logging.getLogger(__name__)

# ntfy.sh caps message bodies; the full brief lives on the web page (Click URL).
MAX_NTFY_LEN = 3500


def _action_url(url: str) -> str:
    """ntfy Actions use commas as delimiters — escape any in the URL."""
    return url.replace(",", "%2C").replace(";", "%2C")


def _ascii_header(value: str) -> str:
    """ntfy headers must be latin-1 safe; strip anything that isn't."""
    return value.encode("ascii", "ignore").decode("ascii").strip() or "PulseBrief"


def _brief_page_url(run_id: int | None = None) -> str | None:
    base = settings.brief_public_url
    if not base:
        return None
    if base.endswith(".html"):
        return base
    suffix = "brief.html" if not base.endswith("/") else "brief.html"
    return f"{base.rstrip('/')}/{suffix}"


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
                chunk_title = title if len(chunks) == 1 else f"{title} ({i + 1}/{len(chunks)})"
                headers = {**base_headers, "Title": _ascii_header(chunk_title)}
                try:
                    resp = client.post(
                        url, content=chunk.encode("utf-8"), headers=headers
                    )
                    if resp.status_code == 400 and "Actions" in headers:
                        logger.warning(
                            "ntfy rejected Actions header; retrying without buttons"
                        )
                        retry_headers = dict(headers)
                        retry_headers.pop("Actions", None)
                        resp = client.post(
                            url, content=chunk.encode("utf-8"), headers=retry_headers
                        )
                    resp.raise_for_status()
                    logger.info(
                        "Published ntfy '%s' %d/%d to %s", chunk_title, i + 1, len(chunks), topic
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

    @staticmethod
    def _action_label(article: Article, index: int) -> str:
        source = (article.source or "").replace(",", " ").replace(";", " ").strip()
        source = source[:22] if source else f"Story {index}"
        return f"Open: {source}"

    def _open_actions(self, articles: list[Article]) -> str | None:
        actions = [
            f"view, {self._action_label(article, i)}, {_action_url(article.url)}"
            for i, article in enumerate(articles[:3], 1)
            if article.url
        ]
        return "; ".join(actions) if actions else None

    def send_intelligence_brief(
        self,
        brief: dict,
        finalists,
        ntfy_cfg: dict | None = None,
        run_id: int | None = None,
    ) -> bool:
        """Thin ntfy banner; full brief opens in browser via Click URL."""
        cfg = ntfy_cfg or {}
        if not cfg.get("send_summary_notification", True):
            return False

        title = digest_notification_title(brief)
        click = _brief_page_url(run_id)
        stories = brief.get("top_stories") or []

        if click:
            body = format_ntfy_teaser()
            actions = None
            logger.info("ntfy tap opens full brief at %s", click)
        else:
            logger.warning(
                "BRIEF_PUBLIC_URL not set — falling back to truncated in-app body. "
                "Set BRIEF_PUBLIC_URL (e.g. GitHub Pages) for full summaries on tap."
            )
            max_per_section = int(cfg.get("max_stories_per_section", 1))
            summary_max = int(cfg.get("summary_max_chars", 280))
            max_body = int(cfg.get("max_body_chars", 3400))
            body = format_ntfy_by_section(
                brief,
                max_per_section=max_per_section,
                summary_max=summary_max,
                max_body_chars=max_body,
            )
            actions = self._brief_actions(stories)

        has_breaking = any(is_breaking_story(s) for s in stories)
        priority = 5 if has_breaking else 3
        return self._publish(
            body,
            title=title,
            click=click,
            priority=priority,
            actions=actions,
        )

    @staticmethod
    def _brief_actions(stories: list[dict]) -> str | None:
        actions = []
        for i, story in enumerate(stories[:3], 1):
            srcs = story.get("sources") or []
            if not srcs:
                continue
            url = srcs[0].get("url")
            name = (srcs[0].get("name") or f"Story {i}")[:22].replace(",", " ")
            if url:
                actions.append(f"view, Open: {name}, {_action_url(url)}")
        return "; ".join(actions) if actions else None

    def send_topic_digest(self, topic: str, articles: list[Article]) -> bool:
        body = self.format_topic_articles(articles)
        lead = articles[0] if articles else None
        click = lead.url if lead else None
        return self._publish(
            body,
            title=topic,
            click=click,
            priority=self._priority_for(articles),
            actions=self._open_actions(articles),
        )
