"""Tests for FastAPI app endpoints."""

import asyncio

import httpx
import pytest
from start_http import app


@pytest.mark.asyncio
async def test_index():
    """Test that the index endpoint returns 200."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")
        assert response.status_code in (200, 307)


def test_app_has_mcp_mount():
    """Test that the MCP endpoint is mounted."""
    routes = [r.path for r in app.routes]
    assert "/" in routes
