"""Stage 13: Groq usage budget manager."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import load_config, settings
from app.models import GroqUsageLog

logger = logging.getLogger(__name__)


class GroqBudgetManager:
    def __init__(self, db: Session, config: dict | None = None) -> None:
        self.db = db
        cfg = (config or load_config()).get("groq", {})
        self.enabled = bool(cfg.get("enabled", True))
        self.max_daily_requests = int(
            settings.groq_max_daily_requests or cfg.get("max_daily_requests", 20)
        )
        self.max_tokens_per_digest = int(
            settings.groq_max_tokens_per_digest or cfg.get("max_tokens_per_digest", 6000)
        )

    def _today_start(self) -> datetime:
        now = datetime.utcnow()
        return datetime(now.year, now.month, now.day)

    def requests_today(self) -> int:
        return (
            self.db.query(GroqUsageLog)
            .filter(GroqUsageLog.created_at >= self._today_start())
            .count()
        )

    def tokens_today(self) -> int:
        rows = (
            self.db.query(GroqUsageLog)
            .filter(GroqUsageLog.created_at >= self._today_start())
            .all()
        )
        return sum((r.estimated_input_tokens or 0) + (r.estimated_output_tokens or 0) for r in rows)

    def can_request(self, purpose: str = "digest") -> bool:
        if not self.enabled or not settings.groq_api_key:
            return False
        if self.requests_today() >= self.max_daily_requests:
            logger.warning("Groq daily request budget exceeded (%d)", self.max_daily_requests)
            return False
        return True

    def record(
        self,
        *,
        purpose: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        self.db.add(
            GroqUsageLog(
                purpose=purpose,
                model=model,
                estimated_input_tokens=input_tokens,
                estimated_output_tokens=output_tokens,
                success=success,
                error=error,
            )
        )
        self.db.commit()

    def stats(self) -> dict:
        since = self._today_start() - timedelta(days=7)
        rows = self.db.query(GroqUsageLog).filter(GroqUsageLog.created_at >= since).all()
        return {
            "requests_today": self.requests_today(),
            "tokens_today": self.tokens_today(),
            "max_daily_requests": self.max_daily_requests,
            "max_tokens_per_digest": self.max_tokens_per_digest,
            "requests_last_7d": len(rows),
            "failures_last_7d": sum(1 for r in rows if not r.success),
        }
