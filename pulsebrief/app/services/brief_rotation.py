"""Track prior digest headlines so each 6h run picks fresh stories."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.models import DigestRun

logger = logging.getLogger(__name__)

LAST_BRIEF_FILE = PROJECT_ROOT / ".last_brief.json"


def heads_from_brief(brief: dict[str, Any]) -> dict[str, str]:
    return {
        s["topic"]: s["headline"]
        for s in brief.get("top_stories", [])
        if s.get("topic") and s.get("headline")
    }


def load_previous_heads(db: Session | None = None) -> dict[str, str]:
    """Last run's headline per topic — file (CI cache) then SQLite."""
    if LAST_BRIEF_FILE.exists():
        try:
            data = json.loads(LAST_BRIEF_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                logger.info("Loaded %d previous headlines from %s", len(data), LAST_BRIEF_FILE.name)
                return {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not read %s", LAST_BRIEF_FILE)

    if db is None:
        return {}

    run = (
        db.query(DigestRun)
        .filter(DigestRun.status == "completed", DigestRun.brief_json.isnot(None))
        .order_by(DigestRun.created_at.desc())
        .first()
    )
    if not run or not run.brief_json:
        return {}
    try:
        brief = json.loads(run.brief_json)
    except json.JSONDecodeError:
        return {}
    heads = heads_from_brief(brief)
    if heads:
        logger.info("Loaded %d previous headlines from digest run #%s", len(heads), run.id)
    return heads


def save_previous_heads(brief: dict[str, Any]) -> None:
    heads = heads_from_brief(brief)
    if not heads:
        return
    try:
        LAST_BRIEF_FILE.write_text(
            json.dumps(heads, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved %d headlines for next rotation check", len(heads))
    except OSError:
        logger.exception("Failed to write %s", LAST_BRIEF_FILE)
