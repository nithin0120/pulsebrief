"""Pydantic schemas for API responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TopicOut(BaseModel):
    name: str
    keywords: list[str]
    queries: list[str]


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    url: str
    source: str
    topic: str
    description: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime
    rank_score: float | None = None
    tldr: str | None = None
    why_it_matters: str | None = None
    digest_position: int | None = None


class ArticleDetailOut(ArticleOut):
    long_summary: str | None = None
    content: str | None = None


class LongSummaryOut(BaseModel):
    id: int
    title: str
    long_summary: str | None


class DigestRunOut(BaseModel):
    id: int
    created_at: datetime
    article_count: int
    status: str
    message: str | None = None
    articles: list[ArticleOut] = []


class HealthOut(BaseModel):
    status: str
    version: str = "1.0.0"
