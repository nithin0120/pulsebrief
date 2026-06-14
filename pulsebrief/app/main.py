"""FastAPI application entry point."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import PUBLIC_DIR, load_topics, log_validation, settings
from app.database import get_db, init_db
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.models import Article, DigestRun
from app.schemas import (
    ArticleDetailOut,
    ArticleOut,
    DigestRunOut,
    HealthOut,
    LongSummaryOut,
    TopicOut,
)
from app.services.digest_service import DigestService
from app.services.brief_html import render_brief_html
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
    log_validation()
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


@app.get("/digest/latest")
def latest_digest_json(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Full brief JSON for the most recent completed run."""
    run = (
        db.query(DigestRun)
        .filter(DigestRun.status == "completed")
        .order_by(DigestRun.created_at.desc())
        .first()
    )
    if not run or not run.brief_json:
        raise HTTPException(status_code=404, detail="No brief available yet")
    return json.loads(run.brief_json)


@app.get("/brief", response_class=HTMLResponse)
@app.get("/brief/latest", response_class=HTMLResponse)
def brief_page(db: Session = Depends(get_db)) -> HTMLResponse:
    """Mobile-friendly full brief — open this from the ntfy notification tap."""
    static = PUBLIC_DIR / "brief.html"
    if static.exists():
        return HTMLResponse(static.read_text(encoding="utf-8"))

    run = (
        db.query(DigestRun)
        .filter(DigestRun.status == "completed")
        .order_by(DigestRun.created_at.desc())
        .first()
    )
    if not run or not run.brief_json:
        return HTMLResponse(
            "<!DOCTYPE html><html><body><p>No brief yet. Run a digest first.</p></body></html>",
            status_code=404,
        )
    brief = json.loads(run.brief_json)
    return HTMLResponse(render_brief_html(brief, run.id))


@app.get("/digest/history")
def digest_history(limit: int = 10, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = DigestService(db)
    return [
        {
            "id": run.id,
            "created_at": run.created_at.isoformat(),
            "article_count": run.article_count,
            "cluster_count": getattr(run, "cluster_count", 0),
            "status": run.status,
            "message": run.message,
        }
        for run in service.get_history(limit=limit)
    ]


@app.get("/clusters/latest")
def latest_clusters(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = DigestService(db)
    return [
        {
            "title": c.title,
            "topic": c.topic,
            "importance": c.importance,
            "summary": c.summary,
            "what_happened_today": c.what_happened_today,
            "why_it_matters": c.why_it_matters,
            "source_links": c.source_links,
            "conflicting_details": c.conflicting_details,
        }
        for c in service.get_latest_clusters()
    ]


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
