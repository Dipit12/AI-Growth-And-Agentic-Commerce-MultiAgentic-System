"""Thin wrapper around the chat model shared by agent nodes. Layer 2 (agents).

Every node takes an injectable `llm_call` callable so tests can supply a fake without hitting the
network or needing an API key; production code falls through to `default_llm_call`, which calls
Claude Haiku via langchain-anthropic when ANTHROPIC_API_KEY is set, otherwise falls back to a local
Ollama model (OLLAMA_MODEL) per CLAUDE.md's tech stack ("local Llama via Ollama as fallback for
cost-sensitive nodes").
"""

from collections.abc import Awaitable, Callable

import httpx
from langchain_anthropic import ChatAnthropic

from app.agents.state import ChatMessage
from app.config import get_settings

LLMCallFn = Callable[[str], Awaitable[str]]

_model_cache: ChatAnthropic | None = None

OLLAMA_TIMEOUT_SECONDS = 120.0


def _get_model() -> ChatAnthropic:
    global _model_cache
    if _model_cache is None:
        settings = get_settings()
        # langchain-anthropic's pydantic-generated __init__ isn't cleanly typed for mypy strict;
        # the kwargs below are verified-correct field names (checked against ChatAnthropic.model_fields).
        _model_cache = ChatAnthropic(  # type: ignore[call-arg]
            model=settings.ROUTER_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,  # type: ignore[arg-type]
            temperature=0,
            max_tokens=1024,
        )
    return _model_cache


async def _anthropic_llm_call(prompt: str) -> str:
    model = _get_model()
    response = await model.ainvoke(prompt)
    content = response.content
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


async def _ollama_llm_call(prompt: str) -> str:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json={"model": settings.OLLAMA_MODEL, "prompt": prompt, "stream": False},
        )
        response.raise_for_status()
        # Ollama's "thinking"-capable models (e.g. deepseek-r1) already separate chain-of-thought
        # into its own "thinking" field — "response" is the clean final text, no tag-stripping needed.
        text: str = response.json()["response"]
        return text.strip()


async def default_llm_call(prompt: str) -> str:
    settings = get_settings()
    if settings.ANTHROPIC_API_KEY:
        return await _anthropic_llm_call(prompt)
    if settings.OLLAMA_MODEL:
        return await _ollama_llm_call(prompt)
    raise RuntimeError(
        "No LLM configured: set ANTHROPIC_API_KEY or OLLAMA_MODEL in the environment/.env."
    )


def format_conversation(messages: list[ChatMessage], limit: int = 10) -> str:
    recent = messages[-limit:]
    return "\n".join(f"{m['role']}: {m['content']}" for m in recent)


def last_user_message(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message["role"] == "user":
            return message["content"]
    return ""
