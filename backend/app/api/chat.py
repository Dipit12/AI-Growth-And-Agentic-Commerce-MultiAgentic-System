"""POST /chat — HTTP interface to the agent graph. Layer 1 (surfaces). Streams via SSE if the
client sends `Accept: text/event-stream`, otherwise returns a single JSON response.

A gated checkout can wait on merchant approval for up to five minutes (see
guardrails/confirmation.py). Blocking the HTTP request that long would be poor UX, so this endpoint
races the graph invocation against a short timeout: if it doesn't finish quickly, the graph keeps
running in the background (the confirmation gate's PendingApproval row already exists — it was
written synchronously before the wait began) and the response tells the caller which
`pending_approval_id` to watch, e.g. via the merchant console WebSocket or a follow-up chat turn.
"""

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.agents.graph import chat_graph
from app.agents.state import AgentState, Cart, ChatMessage, empty_cart
from app.api.deps import get_audit_logger, get_session_store
from app.db.session import async_session_factory
from app.logging_config import get_logger
from app.models.pending_approval import ApprovalStatus, PendingApproval

logger = get_logger("chat_api")
router = APIRouter(tags=["chat"])

GATE_RACE_TIMEOUT_SECONDS = 3.0


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    trace_id: str
    response: str
    cart: Cart
    pending_approval_id: str | None = None
    reason_trace: dict[str, Any] | None = None
    discovery_results: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []


async def _find_latest_pending_approval(trace_id: uuid.UUID) -> uuid.UUID | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(PendingApproval)
            .where(PendingApproval.trace_id == trace_id, PendingApproval.status == ApprovalStatus.PENDING.value)
            .order_by(PendingApproval.created_at.desc())
            .limit(1)
        )
        approval = result.scalar_one_or_none()
        return approval.id if approval is not None else None


async def _start_turn(payload: ChatRequest) -> tuple[str, uuid.UUID, AgentState, "asyncio.Task[AgentState]"]:
    session_store = get_session_store()
    audit = get_audit_logger()
    audit.start()

    session_id = payload.session_id or str(uuid.uuid4())
    trace_id = uuid.uuid4()

    stored_cart = await session_store.get_cart(session_id)
    history = await session_store.get_messages(session_id)
    user_message: ChatMessage = {"role": "user", "content": payload.message}

    state: AgentState = {
        "session_id": session_id,
        "trace_id": str(trace_id),
        "messages": [*history, user_message],  # type: ignore[list-item]
        "cart": stored_cart or empty_cart(),  # type: ignore[arg-type]
    }

    await session_store.append_message(session_id, user_message)

    task: asyncio.Task[AgentState] = asyncio.create_task(chat_graph.ainvoke(state))
    return session_id, trace_id, state, task


@router.post("/chat")
async def chat(payload: ChatRequest, request: Request) -> Response:
    if request.headers.get("accept") == "text/event-stream":
        return await _chat_streaming(payload)
    return await _chat_json(payload)


async def _chat_json(payload: ChatRequest) -> JSONResponse:
    session_id, trace_id, state, task = await _start_turn(payload)
    session_store = get_session_store()

    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=GATE_RACE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        pending_id = await _find_latest_pending_approval(trace_id)
        logger.info("chat.gated_pending_approval", trace_id=str(trace_id), pending_approval_id=str(pending_id))
        response = ChatResponse(
            session_id=session_id,
            trace_id=str(trace_id),
            response=(
                "This exceeds the auto-approve threshold and is waiting for merchant confirmation. "
                "You'll be notified once it's resolved."
            ),
            cart=state["cart"],
            pending_approval_id=str(pending_id) if pending_id else None,
        )
        return JSONResponse(response.model_dump())

    final_response = str(result.get("final_response", ""))
    new_cart = result.get("cart", state["cart"])

    await session_store.append_message(session_id, {"role": "assistant", "content": final_response})
    await session_store.set_cart(session_id, dict(new_cart))

    response = ChatResponse(
        session_id=session_id,
        trace_id=str(trace_id),
        response=final_response,
        cart=new_cart,
        reason_trace=result.get("guardrail_decision"),
        discovery_results=result.get("discovery_results", []),
        recommendations=result.get("recommendations", []),
    )
    return JSONResponse(response.model_dump())


async def _chat_streaming(payload: ChatRequest) -> StreamingResponse:
    session_id, trace_id, state, task = await _start_turn(payload)
    session_store = get_session_store()

    async def event_generator():  # type: ignore[no-untyped-def]
        yield f"event: start\ndata: {json.dumps({'session_id': session_id, 'trace_id': str(trace_id)})}\n\n"

        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=GATE_RACE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            pending_id = await _find_latest_pending_approval(trace_id)
            payload_out = {
                "pending_approval_id": str(pending_id) if pending_id else None,
                "response": "Waiting for merchant confirmation on this action.",
            }
            yield f"event: pending_approval\ndata: {json.dumps(payload_out)}\n\n"
            return

        final_response = str(result.get("final_response", ""))
        new_cart = result.get("cart", state["cart"])
        await session_store.append_message(session_id, {"role": "assistant", "content": final_response})
        await session_store.set_cart(session_id, dict(new_cart))

        for word in final_response.split(" "):
            yield f"data: {json.dumps({'token': word + ' '})}\n\n"
            await asyncio.sleep(0.01)

        done_payload = {
            "cart": new_cart,
            "reason_trace": result.get("guardrail_decision"),
            "discovery_results": result.get("discovery_results", []),
            "recommendations": result.get("recommendations", []),
        }
        yield f"event: done\ndata: {json.dumps(done_payload, default=str)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
