"""Smoke test: the API boots and /health responds with a trace_id."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok_and_trace_id(app_client: AsyncClient) -> None:
    response = await app_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "trace_id" in body
    assert response.headers["x-trace-id"] == body["trace_id"]
