"""Loads agents/prompts/{name}.md and caches the contents in memory. Layer 2 (agents)."""

import functools
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


@functools.lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
