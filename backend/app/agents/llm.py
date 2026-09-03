"""Thin wrapper around the Claude chat model shared by agent nodes. Layer 2 (agents).

Every node takes an injectable `llm_call` callable so tests can supply a fake without hitting the
network or needing an API key; production code falls through to `default_llm_call`, which calls
Claude Haiku via langchain-anthropic.
"""

from collections.abc import Awaitable, Callable

from langchain_anthropic import ChatAnthropic

from app.agents.state import ChatMessage
from app.config import get_settings

LLMCallFn = Callable[[str], Awaitable[str]]

_model_cache: ChatAnthropic | None = None


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


async def default_llm_call(prompt: str) -> str:
    model = _get_model()
    response = await model.ainvoke(prompt)
    content = response.content
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def format_conversation(messages: list[ChatMessage], limit: int = 10) -> str:
    recent = messages[-limit:]
    return "\n".join(f"{m['role']}: {m['content']}" for m in recent)


def last_user_message(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message["role"] == "user":
            return message["content"]
    return ""
