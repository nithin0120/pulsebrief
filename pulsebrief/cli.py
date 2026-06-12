#!/usr/bin/env python3
"""PulseBrief CLI — run digest and interact without Slack."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on path when running as script
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_topics
from app.database import SessionLocal, init_db
from app.services.digest_service import DigestService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pulsebrief.cli")


def cmd_run_digest(args: argparse.Namespace) -> None:
    init_db()
    db = SessionLocal()
    try:
        service = DigestService(db)
        run = asyncio.run(service.run_digest(send=not args.no_send))
        articles = service.get_latest_digest_articles()
        print(f"\nDigest run #{run.id} — {run.article_count} articles ({run.status})\n")
        for article in articles:
            print(f"{article.digest_position}. [{article.topic}] {article.title}")
            print(f"   Source: {article.source}")
            print(f"   TLDR: {article.tldr}")
            print(f"   Why it matters: {article.why_it_matters}")
            print(f"   Link: {article.url}\n")
    finally:
        db.close()


def cmd_more(args: argparse.Namespace) -> None:
    init_db()
    db = SessionLocal()
    try:
        service = DigestService(db)
        result = service.handle_command("more", args.number)
        print(result)
    finally:
        db.close()


def cmd_full(args: argparse.Namespace) -> None:
    init_db()
    db = SessionLocal()
    try:
        service = DigestService(db)
        result = service.handle_command("full", args.number)
        print(result)
    finally:
        db.close()


def cmd_topics(_: argparse.Namespace) -> None:
    topics = load_topics()
    print("Active PulseBrief topics:\n")
    for i, topic in enumerate(topics, 1):
        print(f"{i}. {topic.name}")
        print(f"   Keywords: {', '.join(topic.keywords or topic.queries)}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="PulseBrief — personal AI news digest")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run-digest", help="Fetch, summarize, and deliver digest")
    run_parser.add_argument(
        "--no-send",
        "--no-slack",
        dest="no_send",
        action="store_true",
        help="Skip delivery (Twilio/Slack) and print locally only",
    )
    run_parser.set_defaults(func=cmd_run_digest)

    more_parser = sub.add_parser("more", help="Show longer summary for digest article #")
    more_parser.add_argument("number", type=int)
    more_parser.set_defaults(func=cmd_more)

    full_parser = sub.add_parser("full", help="Show full brief for digest article #")
    full_parser.add_argument("number", type=int)
    full_parser.set_defaults(func=cmd_full)

    topics_parser = sub.add_parser("topics", help="List configured topics")
    topics_parser.set_defaults(func=cmd_topics)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
