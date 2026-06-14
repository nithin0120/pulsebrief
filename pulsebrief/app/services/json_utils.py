"""Small helpers for parsing LLM JSON responses."""

from __future__ import annotations

import json
import re


def extract_json(content: str) -> dict | None:
    """Tolerant JSON extraction: strips fences, then grabs the first {...} block."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None
