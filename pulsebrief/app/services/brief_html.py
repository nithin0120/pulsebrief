"""Render the full intelligence brief as a mobile-friendly HTML page."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from app.services.brief import _section_label, digest_notification_title, is_breaking_story

_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 16px; background: #0f1419; color: #e7e9ea;
  line-height: 1.55; max-width: 720px; margin-inline: auto;
}
header { margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid #2f3336; }
h1 { font-size: 1.35rem; margin: 0 0 4px; font-weight: 700; }
.sub { color: #71767b; font-size: 0.9rem; }
section { margin-bottom: 28px; }
.section-label {
  font-size: 0.8rem; font-weight: 700; letter-spacing: 0.04em;
  text-transform: uppercase; color: #1d9bf0; margin-bottom: 12px;
}
article {
  background: #16181c; border: 1px solid #2f3336; border-radius: 12px;
  padding: 16px; margin-bottom: 12px;
}
article.breaking { border-color: #f4212e; }
h2 { font-size: 1.05rem; margin: 0 0 10px; line-height: 1.35; }
.summary { margin: 0 0 10px; color: #e7e9ea; }
.background { margin: 0 0 10px; color: #aab8c2; font-size: 0.95rem; }
.why { margin: 0 0 10px; color: #aab8c2; font-size: 0.95rem; }
.watch { margin: 0 0 12px; color: #71767b; font-size: 0.9rem; font-style: italic; }
.sources { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
a.btn {
  display: inline-block; padding: 8px 14px; border-radius: 999px;
  background: #1d9bf0; color: #fff !important; text-decoration: none;
  font-size: 0.85rem; font-weight: 600;
}
a.btn:hover { background: #1a8cd8; }
.watchlist { margin-top: 24px; padding-top: 16px; border-top: 1px solid #2f3336; }
.watchlist h3 { font-size: 0.9rem; color: #ffd400; margin: 0 0 8px; }
.watchlist li { margin-bottom: 6px; color: #aab8c2; font-size: 0.9rem; }
"""


def render_brief_html(brief: dict[str, Any], run_id: int | None = None) -> str:
    title = digest_notification_title(brief)
    stories = brief.get("top_stories") or []

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head><body>",
        "<header>",
        f"<h1>{html.escape(title)}</h1>",
        f'<p class="sub">PulseBrief · {len(stories)} topics'
        + (f" · run #{run_id}" if run_id else "")
        + "</p>",
        "</header>",
    ]

    for story in stories:
        topic = story.get("topic", "Other")
        headline = html.escape(story.get("headline") or "")
        what = html.escape(story.get("what_happened") or "").replace("\n", "<br>")
        background = html.escape(story.get("background") or "").strip().replace("\n", "<br>")
        why = html.escape(story.get("why_it_matters") or "").strip()
        watch = html.escape(story.get("what_to_watch_next") or "").strip()
        breaking = is_breaking_story(story)
        cls = ' class="breaking"' if breaking else ""

        parts.append("<section>")
        parts.append(f'<div class="section-label">{html.escape(_section_label(topic))}</div>')
        parts.append(f"<article{cls}>")
        if breaking:
            parts.append('<div class="section-label" style="color:#f4212e;margin-bottom:8px">Breaking</div>')
        parts.append(f"<h2>{headline}</h2>")
        if what:
            parts.append(f'<p class="summary">{what}</p>')
        if background and background != what:
            parts.append(f'<p class="background"><strong>Context:</strong> {background}</p>')
        if why:
            parts.append(f'<p class="why"><strong>Why it matters:</strong> {why}</p>')
        if watch and watch.lower() not in ("follow updates from primary sources.", ""):
            parts.append(f'<p class="watch"><strong>Watch next:</strong> {watch}</p>')
        sources = story.get("sources") or []
        if sources:
            parts.append('<div class="sources">')
            for src in sources[:4]:
                url = src.get("url") or ""
                name = html.escape(src.get("name") or "Source")
                if url:
                    parts.append(
                        f'<a class="btn" href="{html.escape(url, quote=True)}" '
                        f'target="_blank" rel="noopener">{name}</a>'
                    )
            parts.append("</div>")
        parts.append("</article></section>")

    watchlist = brief.get("watchlist") or []
    if watchlist:
        parts.append('<div class="watchlist"><h3>Watchlist</h3><ul>')
        for item in watchlist:
            reason = html.escape((item.get("reason") or "").replace("_", " ").title())
            story = html.escape(item.get("story") or "")
            parts.append(f"<li><strong>{reason}:</strong> {story}</li>")
        parts.append("</ul></div>")

    parts.append("</body></html>")
    return "\n".join(parts)


def export_brief_static(brief: dict[str, Any], run_id: int, out_dir: Path) -> Path:
    """Write brief.html + brief.json for GitHub Pages or static hosting."""
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "brief.html"
    html_path.write_text(render_brief_html(brief, run_id), encoding="utf-8")
    (out_dir / "brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # index.html alias for GitHub Pages root
    (out_dir / "index.html").write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
    return html_path
