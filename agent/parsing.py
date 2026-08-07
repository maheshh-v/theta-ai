"""
Tolerant JSON extraction from model output.

Models wrap JSON in prose, fence it in ```json blocks, escape it twice, or add a
trailing comma. Both the agent loop and the research pipeline depend on getting a
structure back, so parsing lives here once and is reused by both.
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def _candidates(text: str):
    """Yield progressively more aggressive interpretations of `text`."""
    cleaned = (text or "").strip()
    if not cleaned:
        return

    fenced = _FENCE_RE.search(cleaned)
    if fenced:
        yield fenced.group(1).strip()
    yield cleaned

    # Some models return the JSON as an escaped string literal.
    yield cleaned.replace('\\"', '"').replace("\\n", "\n")

    # Fall back to the outermost braces / brackets in the reply.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            yield cleaned[start : end + 1]


def _loads(snippet: str):
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        pass
    # Retry without trailing commas, the single most common malformation.
    try:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", snippet))
    except json.JSONDecodeError:
        return None


def parse_json(text: str):
    """Return the first JSON value found in `text`, or None."""
    for snippet in _candidates(text):
        value = _loads(snippet)
        if value is not None:
            return value
    return None


def parse_object(text: str) -> dict | None:
    """Return the first JSON *object* found in `text`, or None."""
    value = parse_json(text)
    return value if isinstance(value, dict) else None


def parse_list(text: str) -> list | None:
    """Return the first JSON *array* found in `text`, or None.

    Also unwraps the common `{"items": [...]}` / `{"questions": [...]}` shape that
    models produce when asked for a bare list.
    """
    value = parse_json(text)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for candidate in value.values():
            if isinstance(candidate, list):
                return candidate
    return None
