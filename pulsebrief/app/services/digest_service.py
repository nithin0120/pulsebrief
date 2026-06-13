"""Orchestrate fetch, rank, summarize, cluster, store, and deliver the digest."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import load_topics, settings
from app.models import Article, DigestRun, StoryCluster
from app.services import memory as memory_mod
from app.services.brief import format_brief
from app.services.clustering import cluster_items
from app.services.memory import InteractionMemory
from app.services.news_fetcher import RawArticle, normalize_title
from app.services.preferences import PreferenceFilter
from app.services.ranker import Ranker
from app.services.sender import get_sender
from app.services.summarizer import ArticleSummary, Summarizer

logger = logging.getLogger(__name__)


class DigestService:
    def __init__(self, db: Session) -> None:
        self.db = db
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

        prefs = PreferenceFilter()
        mem = InteractionMemory(self.db)

        from app.services.news_fetcher import NewsFetcher

        fetcher = NewsFetcher()
        raw_articles = await fetcher.fetch_for_topics(topics)

        before = len(raw_articles)
        raw_articles = [
            a for a in raw_articles if not prefs.is_muted(a.title, a.description, a.source)
        ]
        if before != len(raw_articles):
            logger.info("Muted filter removed %d articles", before - len(raw_articles))

        categories = [t.name for t in topics]
        topic_keywords_map = {t.name: t.keywords or t.queries for t in topics}
        candidate_total = settings.candidates_per_topic * max(len(topics), 1)
        selected = self.ranker.select_for_digest(
            raw_articles,
            topic_keywords_map,
            per_topic=settings.candidates_per_topic,
            total=candidate_total,
            prefs=prefs,
            memory=mem,
        )

        run = DigestRun(status="running", article_count=0)
        self.db.add(run)
        self.db.flush()

        # Stage 1: cheap batched triage to pick finalists worth full summaries.
        finalists = self._triage_and_select(selected, categories)
        # Stage 2: full rich summaries for finalists only.
        final = self._summarize_finalists(finalists)

        clusters = cluster_items(final)
        cluster_key_by_url = {
            raw.url: cluster.key for cluster in clusters for raw, _, _ in cluster.members
        }

        stored: list[Article] = []
        for position, (raw, score, summary) in enumerate(final, start=1):
            raw.topic = summary.category or raw.topic
            article = self._store_article(raw, score, summary, run.id, position)
            article.cluster_key = cluster_key_by_url.get(raw.url)
            stored.append(article)

        self._store_clusters(clusters, run.id)

        run.article_count = len(stored)
        run.cluster_count = len(clusters)
        run.status = "completed"
        run.message = (
            f"{len(stored)} stories in {len(clusters)} clusters "
            f"(from {len(selected)} candidates, min importance {settings.min_importance})"
        )
        self.db.commit()

        if send and stored:
            self._deliver_by_topic(stored)

        logger.info("Digest run %d completed: %s", run.id, run.message)
        return run

    def _triage_and_select(self, selected, categories):
        """Triage candidates cheaply, drop low-importance/off-topic, cap per topic.

        Returns a list of (raw, score, importance, category) finalists.
        """
        raws = [raw for raw, _ in selected]
        triage = self.summarizer.triage(raws, categories)

        kept: list[tuple] = []
        dropped = 0
        for (raw, score), tr in zip(selected, triage):
            importance, category = tr.importance, tr.category

            if importance is not None and importance < settings.min_importance:
                dropped += 1
                continue
            if category is None:
                if importance is not None:
                    dropped += 1  # AI assessed it but found no clear category
                    continue
                category = raw.topic  # triage unavailable: keep original topic

            kept.append((raw, score, importance, category))

        # Cap per category, keeping the most important.
        by_topic: dict[str, list[tuple]] = {}
        for item in kept:
            by_topic.setdefault(item[3], []).append(item)

        finalists: list[tuple] = []
        for _topic, items in by_topic.items():
            items.sort(key=lambda x: (x[2] or 0, x[1]), reverse=True)
            finalists.extend(items[: settings.max_articles_per_topic])

        finalists.sort(key=lambda x: (x[2] or 0, x[1]), reverse=True)
        logger.info(
            "Triage: %d candidates -> kept %d, dropped %d, %d finalists",
            len(selected), len(kept), dropped, len(finalists),
        )
        return finalists

    def _summarize_finalists(self, finalists):
        """Full rich summary for each finalist (the only expensive AI calls)."""
        final: list[tuple[RawArticle, float, ArticleSummary]] = []
        for raw, score, importance, category in finalists:
            summary = self.summarizer.summarize(raw, [category] if category else None)

            if summary.is_fallback and self.summarizer.provider != "none":
                memory_mod.queue_summary_failure(
                    self.db,
                    title=raw.title,
                    url=raw.url,
                    provider=self.summarizer.provider,
                    error="AI summary failed; used extractive fallback",
                )

            # Triage is the source of truth for importance/category; the rich
            # summary fills them in only when triage had nothing.
            summary.importance = importance if importance is not None else summary.importance
            summary.category = category or summary.category or raw.topic
            final.append((raw, score, summary))
        return final

    def _deliver_by_topic(self, articles: list[Article]) -> None:
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
            print("\n" + format_brief(articles, when=datetime.now()) + "\n")

    def _store_clusters(self, clusters, digest_run_id: int) -> None:
        for cluster in clusters:
            self.db.add(
                StoryCluster(
                    digest_run_id=digest_run_id,
                    cluster_key=cluster.key,
                    title=cluster.title,
                    topic=cluster.topic,
                    importance=cluster.importance,
                    summary=cluster.summary,
                    what_happened_today=cluster.what_happened_today,
                    why_it_matters=cluster.why_it_matters,
                    source_links=json.dumps(cluster.source_links),
                    conflicting_details=cluster.conflicting_details,
                )
            )

    def _store_article(
        self,
        raw: RawArticle,
        score: float,
        summary: ArticleSummary,
        digest_run_id: int,
        position: int,
    ) -> Article:
        article = self.db.query(Article).filter(Article.url == raw.url).first()
        if not article:
            article = Article(url=raw.url)
            self.db.add(article)

        article.title = raw.title
        article.title_normalized = normalize_title(raw.title)
        article.canonical_url = raw.canonical_url
        article.source = raw.source
        article.description = raw.description
        article.content = raw.content
        article.topic = raw.topic
        article.is_opinion = raw.is_opinion
        article.published_at = raw.published_at
        article.fetched_at = datetime.utcnow()
        article.rank_score = score
        article.importance = summary.importance
        article.tldr = summary.tldr
        article.why_it_matters = summary.why_it_matters
        article.bias_or_angle = summary.bias_or_angle
        article.key_entities = json.dumps(summary.key_entities)
        article.follow_up_question = summary.follow_up_question
        article.background = summary.background
        article.what_changed_today = summary.what_changed_today
        article.what_to_watch_next = summary.what_to_watch_next
        article.long_summary = summary.long_summary
        article.digest_run_id = digest_run_id
        article.digest_position = position
        self.db.flush()
        return article

    # --- Read helpers ---------------------------------------------------------

    def _latest_completed_run(self) -> DigestRun | None:
        return (
            self.db.query(DigestRun)
            .filter(DigestRun.status == "completed")
            .order_by(DigestRun.created_at.desc())
            .first()
        )

    def get_latest_digest_articles(self) -> list[Article]:
        run = self._latest_completed_run()
        if not run:
            return []
        return (
            self.db.query(Article)
            .filter(Article.digest_run_id == run.id)
            .order_by(Article.digest_position)
            .all()
        )

    def get_latest_clusters(self) -> list[StoryCluster]:
        run = self._latest_completed_run()
        if not run:
            return []
        return (
            self.db.query(StoryCluster)
            .filter(StoryCluster.digest_run_id == run.id)
            .order_by(StoryCluster.importance.desc())
            .all()
        )

    def get_article_by_digest_position(self, position: int) -> Article | None:
        for article in self.get_latest_digest_articles():
            if article.digest_position == position:
                return article
        return None

    def get_recent_articles(self, limit: int = 20) -> list[Article]:
        return (
            self.db.query(Article).order_by(Article.fetched_at.desc()).limit(limit).all()
        )

    def get_history(self, limit: int = 10) -> list[DigestRun]:
        return (
            self.db.query(DigestRun).order_by(DigestRun.created_at.desc()).limit(limit).all()
        )

    def today_brief(self) -> str:
        return format_brief(self.get_latest_digest_articles(), self.get_latest_clusters())

    # --- Interaction / memory -------------------------------------------------

    def record_action(self, position: int, action: str) -> str:
        article = self.get_article_by_digest_position(position)
        if not article:
            return f"No article at position {position} in the latest digest."
        memory_mod.record_interaction(
            self.db,
            article_url=article.url,
            action=action,
            title_normalized=article.title_normalized,
            source=article.source,
            topic=article.topic,
        )
        verb = {"saved": "Saved", "ignored": "Ignored", "clicked": "Noted"}.get(action, action)
        return f"{verb} #{position}: {article.title}"

    def explain_position(self, position: int) -> str:
        article = self.get_article_by_digest_position(position)
        if not article:
            return f"No article at position {position} in the latest digest."
        deep = self.summarizer.explain(article)
        if deep is None:
            # No AI available — assemble from stored fields.
            return "\n".join(
                [
                    f"Explain #{position}: {article.title}",
                    f"Source: {article.source}",
                    "",
                    f"Background: {article.background or article.long_summary or 'N/A'}",
                    f"What happened today: {article.what_changed_today or article.tldr or 'N/A'}",
                    f"Why it matters: {article.why_it_matters or 'N/A'}",
                    "Who benefits: (needs AI; set GROQ_API_KEY)",
                    "Who is hurt: (needs AI; set GROQ_API_KEY)",
                    f"What to watch next: {article.what_to_watch_next or 'N/A'}",
                ]
            )
        return "\n".join(
            [
                f"Explain #{position}: {article.title}",
                f"Source: {article.source}",
                f"Link: {article.url}",
                "",
                f"Background: {deep.background}",
                f"What happened today: {deep.what_happened_today}",
                f"Why it matters: {deep.why_it_matters}",
                f"Who benefits: {deep.who_benefits}",
                f"Who is hurt: {deep.who_is_hurt}",
                f"What to watch next: {deep.what_to_watch_next}",
            ]
        )

    def handle_command(self, command: str, arg: int | None = None) -> str:
        cmd = command.replace("-", " ")

        if cmd == "topics":
            return self.sender.format_topics([t.name for t in load_topics()])
        if cmd == "today":
            return self.today_brief()
        if cmd == "history":
            return self._format_history()
        if cmd == "run digest":
            return "Use the API POST /digest/run or CLI: python cli.py run"
        if cmd in ("more", "full", "explain", "save", "ignore") and arg is not None:
            if cmd == "more":
                article = self.get_article_by_digest_position(arg)
                return (
                    self.sender.format_more(article)
                    if article
                    else f"No article at position {arg}."
                )
            if cmd == "full":
                article = self.get_article_by_digest_position(arg)
                return (
                    self.sender.format_full(article)
                    if article
                    else f"No article at position {arg}."
                )
            if cmd == "explain":
                return self.explain_position(arg)
            if cmd == "save":
                return self.record_action(arg, "saved")
            if cmd == "ignore":
                return self.record_action(arg, "ignored")

        return "Unknown command. Try: today | topics | history | more <n> | full <n> | explain <n>"

    def _format_history(self) -> str:
        runs = self.get_history()
        if not runs:
            return "No digest history yet."
        lines = ["Digest history:"]
        for run in runs:
            lines.append(
                f"#{run.id} {run.created_at:%Y-%m-%d %H:%M} — {run.article_count} stories "
                f"({run.status})"
            )
        return "\n".join(lines)
