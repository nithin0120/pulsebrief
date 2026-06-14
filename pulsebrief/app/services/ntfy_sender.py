"""Deliver digests via ntfy.sh push notifications (free, no account)."""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.models import Article
from app.services.brief import format_ntfy_summary
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

    @staticmethod
    def _action_label(article: Article, index: int) -> str:
        # Commas/semicolons are ntfy Actions delimiters; strip them from labels.
        source = (article.source or "").replace(",", " ").replace(";", " ").strip()
        source = source[:22] if source else f"Story {index}"
        return f"Open: {source}"

    def _open_actions(self, articles: list[Article]) -> str | None:
        """One 'Open' view-action button per article (ntfy caps actions at 3)."""
        actions = [
            f"view, {self._action_label(article, i)}, {article.url}"
            for i, article in enumerate(articles[:3], 1)
            if article.url
        ]
        return "; ".join(actions) if actions else None

    def send_intelligence_brief(
        self,
        brief: dict,
        finalists,
        ntfy_cfg: dict | None = None,
    ) -> bool:
        """Send concise summary + optional per-section pushes."""
        cfg = ntfy_cfg or {}
        any_sent = False
        max_stories = int(cfg.get("max_top_stories_in_summary", 6))
        breaking = int(cfg.get("breaking_importance", 9))

        # Main summary notification
        if cfg.get("send_summary_notification", True):
            body = format_ntfy_summary(brief, max_stories=max_stories)
            title = brief.get("brief_title", "Morning Brief")
            date = brief.get("date", "")
            click = None
            stories = brief.get("top_stories") or []
            if stories and stories[0].get("sources"):
                click = stories[0]["sources"][0].get("url")
            actions = self._brief_actions(stories)
            if self._publish(
                body,
                title=f"{title} — {date}",
                click=click,
                priority=4 if len(stories) >= 3 else 3,
                actions=actions,
            ):
                any_sent = True

        # Urgent push for breaking clusters
        for story in brief.get("top_stories", []):
            if story.get("confidence") == "high" and len(story.get("sources", [])) >= 2:
                url = story["sources"][0].get("url") if story.get("sources") else None
                urgent = f"🔥 BREAKING [{story.get('topic')}]\n{story.get('headline')}\n{story.get('what_happened', '')[:200]}"
                if self._publish(urgent, title="Breaking", click=url, priority=5):
                    any_sent = True
                break

        # Per-section notifications (optional)
        if cfg.get("send_per_section", True):
            by_topic: dict[str, list] = {}
            for story in brief.get("top_stories", []):
                by_topic.setdefault(story.get("topic", "Other"), []).append(story)

            for topic, stories in by_topic.items():
                if len(by_topic) <= 1:
                    continue
                lines = []
                for i, story in enumerate(stories, 1):
                    lines.append(f"{story.get('headline', '')}")
                    if story.get("what_happened"):
                        lines.append(f"   {story['what_happened'][:160]}")
                    srcs = story.get("sources") or []
                    if srcs:
                        lines.append(f"   Link: {srcs[0].get('url', '')}")
                    lines.append("")
                click = stories[0]["sources"][0].get("url") if stories and stories[0].get("sources") else None
                if self._publish("\n".join(lines).strip(), title=topic, click=click, priority=3):
                    any_sent = True

        return any_sent

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
                actions.append(f"view, Open: {name}, {url}")
        return "; ".join(actions) if actions else None

    def send_topic_digest(self, topic: str, articles: list[Article]) -> bool:
        body = self.format_topic_articles(articles)
        lead = articles[0] if articles else None
        click = lead.url if lead else None  # tapping the notification opens the top story
        return self._publish(
            body,
            title=topic,
            click=click,
            priority=self._priority_for(articles),
            actions=self._open_actions(articles),
        )
