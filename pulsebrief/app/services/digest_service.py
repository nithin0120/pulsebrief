"""Orchestrate the multi-stage local pipeline + one batched Groq brief."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import load_config, load_topics
from app.models import Article, DigestRun, ExtractedText, StoryCluster
from app.services import memory as memory_mod
from app.services.brief import (
    format_brief_json,
    format_story_full,
    format_story_more,
    format_story_sources,
)
from app.services.brief_generator import BriefGenerator
from app.services.groq_budget import GroqBudgetManager
from app.services.memory import InteractionMemory
from app.services.news_fetcher import normalize_title
from app.services.pipeline.article import PipelineArticle, StoryClusterData
from app.services.pipeline.cluster_ranker import ClusterRanker
from app.services.pipeline.clustering import StoryClusterer
from app.services.pipeline.compressor import ContextCompressor
from app.services.pipeline.deduper import deduplicate_articles
from app.services.pipeline.extractor import ArticleExtractor
from app.services.pipeline.junk_filter import filter_junk
from app.services.pipeline.normalizer import normalize_all
from app.services.pipeline.scorer import score_all
from app.services.preferences import PreferenceFilter
from app.services.sender import get_sender
from app.services.sources.orchestrator import FetchOrchestrator

logger = logging.getLogger(__name__)


class DigestService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.config = load_config()
        self.brief_gen = BriefGenerator()
        self.budget = GroqBudgetManager(db, self.config)
        self.sender = get_sender()
        self._last_brief: dict[str, Any] | None = None
        self._last_contexts: list[dict] = []
        self._last_extracted: dict[str, str] = {}
        self._stats: dict[str, Any] = {}

    async def run_digest(self, send: bool = True) -> DigestRun:
        topics = load_topics()
        if not topics:
            run = DigestRun(status="failed", message="No topics configured")
            self.db.add(run)
            self.db.commit()
            return run

        prefs = PreferenceFilter()
        mem = InteractionMemory(self.db)
        dedup_cfg = self.config.get("dedup", {})

        run = DigestRun(status="running")
        self.db.add(run)
        self.db.flush()

        # Stage 1: Fetch
        fetcher = FetchOrchestrator(self.db, self.config)
        raw = await fetcher.fetch_all(topics)
        run.fetched_count = len(raw)

        # Stage 2: Normalize + junk filter
        articles = filter_junk(normalize_all(raw))

        # Mute filter
        before = len(articles)
        articles = [a for a in articles if not prefs.is_muted(a.title, a.description, a.source)]
        if before != len(articles):
            logger.info("Muted filter removed %d articles", before - len(articles))

        # Stage 3: Dedup
        articles = deduplicate_articles(
            articles,
            fuzzy_title_threshold=int(dedup_cfg.get("fuzzy_title_threshold", 88)),
            description_threshold=int(dedup_cfg.get("description_threshold", 90)),
        )

        # Stage 4: Score
        articles = score_all(articles, topics, prefs=prefs, memory=mem, config=self.config)

        # Stage 5: Cluster
        clusters = StoryClusterer(self.config).cluster(articles)

        # Stage 6: Rank clusters
        finalists = ClusterRanker(self.config).select(clusters, topics)

        # Stage 7: Extract full text for finalists only
        extracted = ArticleExtractor(self.config).enrich_clusters(finalists)
        self._persist_extracted(extracted)

        # Stage 8: Compress contexts (cap count for Groq TPM budget)
        max_for_groq = min(len(finalists), 4)
        contexts = ContextCompressor(self.config).build_contexts(
            finalists, extracted, max_clusters=max_for_groq
        )
        self._last_contexts = [c.to_dict() for c in contexts]
        self._last_extracted = extracted

        # Stage 9: ONE batched Groq call (or fallback)
        groq_used = 0
        if self.budget.can_request("digest"):
            brief, est_in, est_out = self.brief_gen.generate_digest(
                contexts,
                clusters_for_fallback=finalists,
                max_tokens=self.budget.max_tokens_per_digest,
            )
            groq_used = 1
            self.budget.record(
                purpose="digest",
                model=self.brief_gen._model,
                input_tokens=est_in,
                output_tokens=est_out,
                success=not brief.get("fallback"),
            )
        else:
            from app.services.pipeline.fallback import build_fallback_brief

            brief = build_fallback_brief(contexts, finalists)

        self._last_brief = brief

        # Persist articles + clusters + digest run
        self._store_pipeline_results(run, finalists, brief)

        run.article_count = len(brief.get("top_stories", []))
        run.cluster_count = len(finalists)
        run.groq_requests = groq_used
        run.brief_json = json.dumps(brief, ensure_ascii=False)
        run.status = "completed"
        run.message = (
            f"Fetched {run.fetched_count} -> {len(articles)} after dedup -> "
            f"{len(finalists)} finalist clusters; Groq calls: {groq_used}"
        )
        self.db.commit()

        self._stats = {
            "fetched": run.fetched_count,
            "after_dedup": len(articles),
            "clusters": len(clusters),
            "finalists": len(finalists),
            "groq_requests": groq_used,
            "tokens_saved_estimate": max(0, len(articles) - len(finalists)) * 800,
        }

        if send and brief.get("top_stories"):
            self._deliver_brief(brief, finalists)

        logger.info("Digest run %d completed: %s", run.id, run.message)
        return run

    def _persist_extracted(self, extracted: dict[str, str]) -> None:
        for url, text in extracted.items():
            row = self.db.query(ExtractedText).filter(ExtractedText.url == url).first()
            if row:
                row.text = text
            else:
                self.db.add(ExtractedText(url=url, text=text))
        self.db.flush()

    def _store_pipeline_results(
        self,
        run: DigestRun,
        finalists: list[StoryClusterData],
        brief: dict[str, Any],
    ) -> None:
        story_map = {s.get("cluster_id"): s for s in brief.get("top_stories", [])}

        for i, cluster in enumerate(finalists, 1):
            story = story_map.get(i, {})
            links = [{"source": s, "url": a.url} for s, a in zip(cluster.source_names, cluster.articles)]
            self.db.add(
                StoryCluster(
                    digest_run_id=run.id,
                    cluster_key=cluster.cluster_id,
                    title=cluster.cluster_title,
                    topic=cluster.topic,
                    importance=int(cluster.importance_score),
                    summary=story.get("what_happened"),
                    what_happened_today=story.get("what_happened"),
                    why_it_matters=story.get("why_it_matters"),
                    source_links=json.dumps(links),
                    conflicting_details=(story.get("source_comparison") or {}).get("differences"),
                )
            )

            if cluster.representative:
                rep = cluster.representative
                article = self._upsert_article(rep, run.id, i, story)
                rep.db_id = article.id

    def _upsert_article(
        self, raw: PipelineArticle, digest_run_id: int, position: int, story: dict
    ) -> Article:
        article = self.db.query(Article).filter(Article.url == raw.url).first()
        if not article:
            article = Article(url=raw.url)
            self.db.add(article)

        article.title = raw.title
        article.title_normalized = raw.title_normalized or normalize_title(raw.title)
        article.canonical_url = raw.canonical_url
        article.source = raw.source
        article.description = raw.description
        article.content = raw.content
        article.topic = raw.topic
        article.is_opinion = raw.is_opinion
        article.published_at = raw.published_at
        article.fetched_at = datetime.utcnow()
        article.rank_score = raw.importance_score
        article.importance = int(raw.importance_score)
        article.cluster_key = raw.article_id
        article.tldr = story.get("what_happened")
        article.why_it_matters = story.get("why_it_matters")
        article.background = story.get("background")
        article.what_changed_today = story.get("what_happened")
        article.what_to_watch_next = story.get("what_to_watch_next")
        article.long_summary = story.get("background")
        article.digest_run_id = digest_run_id
        article.digest_position = position
        self.db.flush()
        return article

    def _deliver_brief(self, brief: dict[str, Any], finalists: list[StoryClusterData]) -> None:
        ntfy_cfg = self.config.get("ntfy", {})
        if hasattr(self.sender, "send_intelligence_brief"):
            sent = self.sender.send_intelligence_brief(brief, finalists, ntfy_cfg)
            if sent:
                return

        # Console fallback
        print("\n" + format_brief_json(brief) + "\n")

    def _load_latest_brief(self) -> dict[str, Any] | None:
        if self._last_brief:
            return self._last_brief
        run = self._latest_completed_run()
        if run and run.brief_json:
            try:
                return json.loads(run.brief_json)
            except json.JSONDecodeError:
                return None
        return None

    def _story_by_position(self, position: int) -> dict[str, Any] | None:
        brief = self._load_latest_brief()
        if not brief:
            return None
        for story in brief.get("top_stories", []):
            if story.get("cluster_id") == position:
                return story
        stories = brief.get("top_stories", [])
        if 1 <= position <= len(stories):
            return stories[position - 1]
        return None

    def _latest_completed_run(self) -> DigestRun | None:
        return (
            self.db.query(DigestRun)
            .filter(DigestRun.status == "completed")
            .order_by(DigestRun.created_at.desc())
            .first()
        )

    def today_brief(self) -> str:
        brief = self._load_latest_brief()
        return format_brief_json(brief)

    def get_history(self, limit: int = 10) -> list[DigestRun]:
        return (
            self.db.query(DigestRun).order_by(DigestRun.created_at.desc()).limit(limit).all()
        )

    def get_stats(self) -> dict[str, Any]:
        run = self._latest_completed_run()
        budget = self.budget.stats()
        return {
            **self._stats,
            "latest_run_id": run.id if run else None,
            "latest_fetched": run.fetched_count if run else 0,
            "latest_finalists": run.cluster_count if run else 0,
            "groq_budget": budget,
        }

    def explain_position(self, position: int) -> str:
        story = self._story_by_position(position)
        if not story:
            return f"No story at position {position}."
        ctx = next((c for c in self._last_contexts if c.get("cluster_id") == position), None)
        if not ctx:
            ctx = {"headline": story.get("headline"), **story}
        if self.budget.can_request("explain"):
            result = self.brief_gen.generate_explain(ctx)
            if result:
                self.budget.record(purpose="explain", model=self.brief_gen._model, success=True)
                return f"Explain #{position}\n\n{result}"
        return format_story_more(story)

    def compare_position(self, position: int) -> str:
        story = self._story_by_position(position)
        if not story:
            return f"No story at position {position}."
        ctx = next((c for c in self._last_contexts if c.get("cluster_id") == position), None) or story
        if self.budget.can_request("compare"):
            result = self.brief_gen.generate_compare(ctx)
            if result:
                self.budget.record(purpose="compare", model=self.brief_gen._model, success=True)
                return result
        comp = story.get("source_comparison") or {}
        lines = [f"Compare #{position}: {story.get('headline', '')}", ""]
        for k in ("agreement", "differences", "framing_or_bias"):
            if comp.get(k):
                lines.append(f"{k.replace('_', ' ').title()}: {comp[k]}")
        return "\n".join(lines) if len(lines) > 2 else "Not enough source data to compare."

    def sources_position(self, position: int) -> str:
        story = self._story_by_position(position)
        if not story:
            return f"No story at position {position}."
        return format_story_sources(story)

    def handle_command(self, command: str, arg: int | None = None) -> str:
        cmd = command.replace("-", " ")
        if cmd == "today":
            return self.today_brief()
        if cmd == "history":
            return self._format_history()
        if cmd == "stats":
            return self._format_stats()
        if cmd == "more" and arg:
            story = self._story_by_position(arg)
            return format_story_more(story) if story else f"No story at #{arg}."
        if cmd == "full" and arg:
            story = self._story_by_position(arg)
            if not story:
                return f"No story at #{arg}."
            extracted: dict[str, str] = {}
            for s in story.get("sources", []):
                url = s.get("url")
                if not url:
                    continue
                row = self.db.query(ExtractedText).filter(ExtractedText.url == url).first()
                if row and row.text:
                    extracted[url] = row.text
            return format_story_full(story, extracted or self._last_extracted)
        if cmd == "explain" and arg:
            return self.explain_position(arg)
        if cmd == "compare" and arg:
            return self.compare_position(arg)
        if cmd == "sources" and arg:
            return self.sources_position(arg)
        if cmd == "save" and arg:
            return self.record_action(arg, "saved")
        if cmd == "ignore" and arg:
            return self.record_action(arg, "ignored")
        return "Unknown command."

    def record_action(self, position: int, action: str) -> str:
        story = self._story_by_position(position)
        if not story:
            return f"No story at position {position}."
        url = (story.get("sources") or [{}])[0].get("url", "")
        memory_mod.record_interaction(
            self.db,
            article_url=url or story.get("headline", ""),
            action=action,
            title_normalized=normalize_title(story.get("headline", "")),
            source=(story.get("sources") or [{}])[0].get("name"),
            topic=story.get("topic"),
        )
        verb = {"saved": "Saved", "ignored": "Ignored"}.get(action, action)
        return f"{verb} #{position}: {story.get('headline', '')}"

    def _format_history(self) -> str:
        runs = self.get_history()
        if not runs:
            return "No digest history yet."
        lines = ["Digest history:"]
        for run in runs:
            lines.append(
                f"#{run.id} {run.created_at:%Y-%m-%d %H:%M} — "
                f"fetched {run.fetched_count}, {run.cluster_count} clusters, "
                f"{run.article_count} stories ({run.status})"
            )
        return "\n".join(lines)

    def _format_stats(self) -> str:
        s = self.get_stats()
        b = s.get("groq_budget", {})
        lines = [
            "PulseBrief stats",
            f"Latest run: #{s.get('latest_run_id')}",
            f"Fetched (last run): {s.get('latest_fetched', 0)}",
            f"Finalist clusters: {s.get('latest_finalists', 0)}",
            f"Groq requests today: {b.get('requests_today', 0)} / {b.get('max_daily_requests', 20)}",
            f"Est. tokens today: {b.get('tokens_today', 0)}",
            f"Est. tokens saved (last run): ~{s.get('tokens_saved_estimate', 0)}",
            f"Groq failures (7d): {b.get('failures_last_7d', 0)}",
        ]
        return "\n".join(lines)

    # Legacy API compatibility
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

    def get_article_by_digest_position(self, position: int) -> Article | None:
        run = self._latest_completed_run()
        if not run:
            return None
        return (
            self.db.query(Article)
            .filter(Article.digest_run_id == run.id, Article.digest_position == position)
            .first()
        )
