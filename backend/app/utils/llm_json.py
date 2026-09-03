"""Tolerant JSON extraction from an LLM's free-text response — models sometimes wrap JSON in prose
or code fences. Used by agent nodes that need structured output (cart_manager, discovery)."""

import json
import re
from typing import Any

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {text!r}")
    result: dict[str, Any] = json.loads(match.group(0))
    return result
