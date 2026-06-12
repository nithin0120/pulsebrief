"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import load_topics, settings
from app.database import get_db, init_db
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.models import Article
from app.schemas import (
    ArticleDetailOut,
    ArticleOut,
    DigestRunOut,
    HealthOut,
    LongSummaryOut,
    TopicOut,
)
from app.services.digest_service import DigestService
from app.services.slack_sender import parse_slack_command
from app.services.twilio_sender import parse_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    logger.info("PulseBrief started")
    yield
    stop_scheduler()
    logger.info("PulseBrief stopped")


app = FastAPI(title="PulseBrief", version="1.0.0", lifespan=lifespan)


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok")


@app.get("/topics", response_model=list[TopicOut])
def get_topics() -> list[TopicOut]:
    topics = load_topics()
    return [
        TopicOut(name=t.name, keywords=t.keywords, queries=t.queries)
        for t in topics
    ]


@app.post("/digest/run", response_model=DigestRunOut)
async def run_digest(
    send: bool = True,
    db: Session = Depends(get_db),
) -> DigestRunOut:
    service = DigestService(db)
    run = await service.run_digest(send=send)
    articles = (
        db.query(Article)
        .filter(Article.digest_run_id == run.id)
        .order_by(Article.digest_position)
        .all()
    )
    return DigestRunOut(
        id=run.id,
        created_at=run.created_at,
        article_count=run.article_count,
        status=run.status,
        message=run.message,
        articles=[ArticleOut.model_validate(a) for a in articles],
    )


@app.get("/articles/recent", response_model=list[ArticleOut])
def recent_articles(
    limit: int = 20,
    db: Session = Depends(get_db),
) -> list[ArticleOut]:
    service = DigestService(db)
    articles = service.get_recent_articles(limit=limit)
    return [ArticleOut.model_validate(a) for a in articles]


@app.get("/articles/{article_id}", response_model=ArticleDetailOut)
def get_article(article_id: int, db: Session = Depends(get_db)) -> ArticleDetailOut:
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return ArticleDetailOut.model_validate(article)


@app.get("/articles/{article_id}/long-summary", response_model=LongSummaryOut)
def get_long_summary(article_id: int, db: Session = Depends(get_db)) -> LongSummaryOut:
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return LongSummaryOut(
        id=article.id,
        title=article.title,
        long_summary=article.long_summary,
    )


@app.post("/slack/events")
async def slack_events(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Handle Slack slash commands and message replies."""
    body = await request.body()
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        payload = await request.json()
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge")}
        event = payload.get("event", {})
        text = event.get("text", "")
    else:
        form = await request.form()
        text = str(form.get("text", ""))

    command, arg = parse_slack_command(text)
    service = DigestService(db)

    if command == "run digest":
        run = await service.run_digest(send=True)
        return {"text": f"Digest run complete: {run.article_count} articles."}

    response = service.handle_command(command, arg)
    return {"text": response}


def _twiml(message: str) -> Response:
    escaped = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escaped}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


@app.post("/twilio/sms")
async def twilio_sms(request: Request, db: Session = Depends(get_db)) -> Response:
    """Handle inbound Twilio SMS/WhatsApp replies and respond with TwiML.

    Configure this URL as the messaging webhook for your Twilio number
    (e.g. https://<public-host>/twilio/sms).
    """
    form = await request.form()
    text = str(form.get("Body", ""))

    command, arg = parse_command(text)
    service = DigestService(db)

    if command == "run digest":
        run = await service.run_digest(send=False)
        return _twiml(f"Digest run complete: {run.article_count} articles.")

    reply = service.handle_command(command, arg)
    return _twiml(reply)
