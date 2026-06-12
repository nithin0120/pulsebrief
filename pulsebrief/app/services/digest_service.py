"""Orchestrate fetch, rank, summarize, store, and deliver digest."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import TopicConfig, load_topics, settings
from app.models import Article, DigestRun
from app.services.news_fetcher import RawArticle, normalize_title
from app.services.ranker import Ranker
from app.services.sender import get_sender
from app.services.summarizer import Summarizer

logger = logging.getLogger(__name__)


class DigestService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.fetcher = None  # set async
        self.ranker = Ranker()
        self.summarizer = Summarizer()
        self.sender = get_sender()

    async def run_digest(self, send: bool = True) -> DigestRun:
        topics = load_topics()
        if not topics:
            run = DigestRun(status="failed", message="No topics configured", article_count=0)
            self.db.add(run)
            self.db.commit()
            return run

        from app.services.news_fetcher import NewsFetcher

        fetcher = NewsFetcher()
        raw_articles = await fetcher.fetch_for_topics(topics)

        categories = [t.name for t in topics]
        topic_keywords_map = {t.name: t.keywords or t.queries for t in topics}
        candidate_total = settings.candidates_per_topic * max(len(topics), 1)
        selected = self.ranker.select_for_digest(
            raw_articles,
            topic_keywords_map,
            per_topic=settings.candidates_per_topic,
            total=candidate_total,
        )

        run = DigestRun(status="running", article_count=0)
        self.db.add(run)
        self.db.flush()

        kept = self._summarize_and_filter(selected, categories)
        final = self._cap_per_topic(kept)

        stored: list[Article] = []
        for position, (raw, score, summary) in enumerate(final, start=1):
            raw.topic = summary.category or raw.topic
            article = self._store_article(raw, score, run.id, position)
            article.tldr = summary.tldr
            article.why_it_matters = summary.why_it_matters
            article.long_summary = summary.long_summary
            stored.append(article)

        run.article_count = len(stored)
        run.status = "completed"
        run.message = (
            f"Digest: {len(stored)} kept of {len(selected)} candidates "
            f"(min importance {settings.min_importance})"
        )
        self.db.commit()

        if send and stored:
            self._deliver_by_topic(stored)

        logger.info(
            "Digest run %d completed: %d kept of %d candidates",
            run.id,
            len(stored),
            len(selected),
        )
        return run

    def _summarize_and_filter(self, selected, categories):
        """Summarize candidates and drop low-importance or off-topic ones."""
        kept: list[tuple] = []
        dropped = 0
        for raw, score in selected:
            summary = self.summarizer.summarize(raw, categories)

            if summary.importance is not None and summary.importance < settings.min_importance:
                dropped += 1
                continue

            if summary.category is None:
                # AI assessed it but found no clear category -> off-topic, drop.
                # If the AI was unavailable (importance also None), keep original topic.
                if summary.importance is not None:
                    dropped += 1
                    continue
                summary.category = raw.topic

            kept.append((raw, score, summary))

        logger.info("Filtered candidates: kept %d, dropped %d", len(kept), dropped)
        return kept

    def _cap_per_topic(self, kept):
        """Keep the top N most important per category; order overall by importance."""
        by_topic: dict[str, list[tuple]] = {}
        for raw, score, summary in kept:
            by_topic.setdefault(summary.category, []).append((raw, score, summary))

        final: list[tuple] = []
        for _topic, items in by_topic.items():
            items.sort(key=lambda x: (x[2].importance or 0, x[1]), reverse=True)
            final.extend(items[: settings.max_articles_per_topic])

        final.sort(key=lambda x: (x[2].importance or 0, x[1]), reverse=True)
        return final

    def _deliver_by_topic(self, articles: list[Article]) -> None:
        """Send one notification per topic (title = topic, body = its headlines)."""
        by_topic: dict[str, list[Article]] = {}
        for article in articles:
            by_topic.setdefault(article.topic, []).append(article)

        any_sent = False
        for topic, topic_articles in by_topic.items():
            if self.sender.send_topic_digest(topic, topic_articles):
                any_sent = True

        if not any_sent:
            logger.info(
                "Digest saved locally; %s delivery skipped or failed",
                settings.delivery_channel,
            )
            print("\n--- PulseBrief Digest (local) ---\n")
            for topic, topic_articles in by_topic.items():
                print(f"[{topic}]")
                print(self.sender.format_topic_articles(topic_articles))
                print()

    def _store_article(
        self,
        raw: RawArticle,
        score: float,
        digest_run_id: int,
        position: int,
    ) -> Article:
        existing = self.db.query(Article).filter(Article.url == raw.url).first()
        if existing:
            existing.title = raw.title
            existing.title_normalized = normalize_title(raw.title)
            existing.source = raw.source
            existing.description = raw.description
            existing.content = raw.content
            existing.topic = raw.topic
            existing.published_at = raw.published_at
            existing.fetched_at = datetime.utcnow()
            existing.rank_score = score
            existing.digest_run_id = digest_run_id
            existing.digest_position = position
            return existing

        article = Article(
            title=raw.title,
            title_normalized=normalize_title(raw.title),
            url=raw.url,
            source=raw.source,
            description=raw.description,
            content=raw.content,
            topic=raw.topic,
            published_at=raw.published_at,
            fetched_at=datetime.utcnow(),
            rank_score=score,
            digest_run_id=digest_run_id,
            digest_position=position,
        )
        self.db.add(article)
        self.db.flush()
        return article

    def get_latest_digest_articles(self) -> list[Article]:
        latest_run = (
            self.db.query(DigestRun)
            .filter(DigestRun.status == "completed")
            .order_by(DigestRun.created_at.desc())
            .first()
        )
        if not latest_run:
            return []
        return (
            self.db.query(Article)
            .filter(Article.digest_run_id == latest_run.id)
            .order_by(Article.digest_position)
            .all()
        )

    def get_article_by_digest_position(self, position: int) -> Article | None:
        articles = self.get_latest_digest_articles()
        for article in articles:
            if article.digest_position == position:
                return article
        return None

    def get_recent_articles(self, limit: int = 20) -> list[Article]:
        return (
            self.db.query(Article)
            .order_by(Article.fetched_at.desc())
            .limit(limit)
            .all()
        )

    def handle_command(self, command: str, arg: int | None = None) -> str:
        """Resolve a command to its reply text. Delivery is left to the caller."""
        cmd = command.replace("-", " ")

        if cmd == "topics":
            topics = load_topics()
            return self.sender.format_topics([t.name for t in topics])

        if cmd == "run digest":
            return "Use the API POST /digest/run or CLI: python cli.py run-digest"

        if cmd == "more" and arg is not None:
            article = self.get_article_by_digest_position(arg)
            if not article:
                return f"No article found at position {arg} in the latest digest."
            return self.sender.format_more(article)

        if cmd == "full" and arg is not None:
            article = self.get_article_by_digest_position(arg)
            if not article:
                return f"No article found at position {arg} in the latest digest."
            return self.sender.format_full(article)

        return "Unknown command. Try: topics | run digest | more <n> | full <n>"
