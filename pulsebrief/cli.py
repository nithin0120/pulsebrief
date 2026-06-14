#!/usr/bin/env python3
"""PulseBrief CLI — run the digest and interact locally."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_topics, log_validation
from app.database import SessionLocal, init_db
from app.services import preferences as prefs_mod
from app.services.digest_service import DigestService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pulsebrief.cli")


def _with_service(fn):
    """Run `fn(service, args)` inside an initialized DB session."""

    def wrapper(args: argparse.Namespace) -> None:
        init_db()
        db = SessionLocal()
        try:
            fn(DigestService(db), args)
        finally:
            db.close()

    return wrapper


@_with_service
def cmd_run(service: DigestService, args: argparse.Namespace) -> None:
    log_validation()
    run = asyncio.run(service.run_digest(send=not args.no_send))
    print(f"\nDigest run #{run.id} — {run.article_count} stories "
          f"in {run.cluster_count} clusters ({run.status})\n")
    print(service.today_brief())


@_with_service
def cmd_today(service: DigestService, _: argparse.Namespace) -> None:
    print(service.today_brief())


@_with_service
def cmd_more(service: DigestService, args: argparse.Namespace) -> None:
    print(service.handle_command("more", args.number))


@_with_service
def cmd_full(service: DigestService, args: argparse.Namespace) -> None:
    print(service.handle_command("full", args.number))


@_with_service
def cmd_explain(service: DigestService, args: argparse.Namespace) -> None:
    print(service.explain_position(args.number))


@_with_service
def cmd_compare(service: DigestService, args: argparse.Namespace) -> None:
    print(service.compare_position(args.number))


@_with_service
def cmd_sources(service: DigestService, args: argparse.Namespace) -> None:
    print(service.sources_position(args.number))


@_with_service
def cmd_stats(service: DigestService, _: argparse.Namespace) -> None:
    print(service._format_stats())


@_with_service
def cmd_history(service: DigestService, _: argparse.Namespace) -> None:
    print(service._format_history())


@_with_service
def cmd_save(service: DigestService, args: argparse.Namespace) -> None:
    print(service.record_action(args.number, "saved"))


@_with_service
def cmd_ignore(service: DigestService, args: argparse.Namespace) -> None:
    print(service.record_action(args.number, "ignored"))


def cmd_topics(_: argparse.Namespace) -> None:
    topics = load_topics()
    print("Active PulseBrief topics:\n")
    for i, topic in enumerate(topics, 1):
        print(f"{i}. {topic.name}")
        print(f"   Keywords: {', '.join(topic.keywords or topic.queries)}")
        print()


def cmd_mute_keyword(args: argparse.Namespace) -> None:
    ok = prefs_mod.mute_keyword(args.value)
    print(f"Muted keyword: {args.value}" if ok else f"Already muted: {args.value}")
    print("Commit & push preferences.yaml for it to apply to your scheduled cloud digest.")


def cmd_mute_source(args: argparse.Namespace) -> None:
    ok = prefs_mod.mute_source(args.value)
    print(f"Muted source: {args.value}" if ok else f"Already muted: {args.value}")
    print("Commit & push preferences.yaml for it to apply to your scheduled cloud digest.")


def cmd_add_topic(args: argparse.Namespace) -> None:
    ok = prefs_mod.add_topic(args.value)
    print(f"Added topic: {args.value}" if ok else f"Topic already exists: {args.value}")


def cmd_remove_topic(args: argparse.Namespace) -> None:
    ok = prefs_mod.remove_topic(args.value)
    print(f"Removed topic: {args.value}" if ok else f"Topic not found: {args.value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PulseBrief — personal AI news digest")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", aliases=["run-digest"], help="Fetch, summarize, deliver")
    run_parser.add_argument(
        "--no-send", "--no-slack", dest="no_send", action="store_true",
        help="Skip delivery and print the brief locally only",
    )
    run_parser.set_defaults(func=cmd_run)

    sub.add_parser("today", help="Print the latest brief").set_defaults(func=cmd_today)
    sub.add_parser("topics", help="List configured topics").set_defaults(func=cmd_topics)
    sub.add_parser("history", help="Show recent digest runs").set_defaults(func=cmd_history)
    sub.add_parser("stats", help="Show pipeline and Groq usage stats").set_defaults(func=cmd_stats)

    for name, fn, helptext in [
        ("more", cmd_more, "Longer summary for digest story #"),
        ("full", cmd_full, "Full brief for digest story #"),
        ("explain", cmd_explain, "Deep explainer for digest story # (Groq on demand)"),
        ("compare", cmd_compare, "Compare how sources frame story # (Groq on demand)"),
        ("sources", cmd_sources, "List all source links for story #"),
        ("save", cmd_save, "Remember that you liked story #"),
        ("ignore", cmd_ignore, "Down-rank story # going forward"),
    ]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("number", type=int)
        p.set_defaults(func=fn)

    for name, fn, helptext in [
        ("mute-keyword", cmd_mute_keyword, "Never show stories matching a keyword"),
        ("mute-source", cmd_mute_source, "Never show stories from a source"),
        ("add-topic", cmd_add_topic, "Add a topic to topics.yaml"),
        ("remove-topic", cmd_remove_topic, "Remove a topic from topics.yaml"),
    ]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("value", type=str)
        p.set_defaults(func=fn)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
