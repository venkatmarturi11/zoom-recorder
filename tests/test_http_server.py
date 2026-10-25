"""Automated tests for UptimeRobot HTTP keep-alive server."""

from unittest.mock import MagicMock
import pytest
from aiohttp import web
from app.http_server import create_health_app, handle_health, handle_root


@pytest.mark.asyncio
async def test_handle_root():
    """Verify root keep-alive response for UptimeRobot."""
    mock_request = MagicMock(spec=web.Request)
    resp = await handle_root(mock_request)
    assert resp.status == 200
    assert "ONLINE" in resp.text


@pytest.mark.asyncio
async def test_handle_health():
    """Verify health endpoint returns JSON health status."""
    mock_request = MagicMock(spec=web.Request)
    resp = await handle_health(mock_request)
    assert resp.status == 200
    assert "healthy" in resp.text
    assert "zoom-telegram-recorder" in resp.text


def test_create_health_app():
    """Verify required routes exist in aiohttp application."""
    app = create_health_app()
    routes = [r.resource.canonical for r in app.router.routes()]
    assert "/" in routes
    assert "/health" in routes
    assert "/ping" in routes
