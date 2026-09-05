"""FastAPI entrypoint — Layer 1 (surfaces). Wires middleware, routers, and startup hooks."""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.audit import router as audit_router
from app.api.chat import router as chat_router
from app.api.deps import get_audit_logger
from app.api.merchant import router as merchant_router
from app.api.ws import router as ws_router
from app.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_audit_logger().start()
    logger.info("app.startup")
    yield
    await get_audit_logger().stop()
    logger.info("app.shutdown")


app = FastAPI(title="Razorpay Buildathon — Agentic Commerce", lifespan=lifespan)

# The chat widget / merchant console / audit viewer run on Vite's dev server (a different origin
# from the API), so the browser blocks every fetch without this — surfaces client-side as a generic
# "Failed to fetch" with no useful error, since the browser blocks the response before JS ever sees it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(audit_router)
app.include_router(chat_router)
app.include_router(merchant_router)
app.include_router(ws_router)


@app.middleware("http")
async def bind_trace_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(trace_id=trace_id)
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["x-trace-id"] = trace_id
    return response


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    logger.info("health.check")
    return {"status": "ok", "trace_id": request.state.trace_id}
