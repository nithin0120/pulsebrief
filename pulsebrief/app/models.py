"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("url", name="uq_article_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    title_normalized: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="Unknown")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    is_opinion: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    rank_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    importance: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Rich Groq summary fields.
    tldr: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    bias_or_angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_entities: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    follow_up_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_changed_today: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_to_watch_next: Mapped[str | None] = mapped_column(Text, nullable=True)
    long_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    cluster_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    digest_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    digest_position: Mapped[int | None] = mapped_column(Integer, nullable=True)


class StoryCluster(Base):
    __tablename__ = "story_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    cluster_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    importance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_happened_today: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_links: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    conflicting_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DigestRun(Base):
    __tablename__ = "digest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    article_count: Mapped[int] = mapped_column(Integer, default=0)
    cluster_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    brief_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # cached intelligence brief
    groq_requests: Mapped[int] = mapped_column(Integer, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)


class GroqUsageLog(Base):
    __tablename__ = "groq_usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)  # digest/explain/compare
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExtractedText(Base):
    __tablename__ = "extracted_texts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ArticleInteraction(Base):
    """Local record of how the user engaged with articles (memory)."""

    __tablename__ = "article_interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    title_normalized: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    topic: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # saved/ignored/clicked
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
