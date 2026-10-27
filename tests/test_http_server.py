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
    assert "/oauth/callback" in routes
    assert "/login" in routes
    assert "/api/auth/submit" in routes
    assert "/api/auth/mfa" in routes
    assert "/api/auth/import" in routes
    assert "/api/auth/status" in routes


@pytest.mark.asyncio
async def test_handle_login_page():
    """Verify login page rendering with valid token and rejection with missing/invalid token."""
    from app.http_server import handle_login_page
    from app.login_session import get_login_session_manager

    # 1. Without token: 401 Unauthorized
    req_invalid = MagicMock(spec=web.Request)
    req_invalid.query = {}
    resp_invalid = await handle_login_page(req_invalid)
    assert resp_invalid.status == 401
    assert "Invalid or Expired Link" in resp_invalid.text

    # 2. With valid token: 200 OK HTML
    login_mgr = get_login_session_manager()
    session = login_mgr.create_session(telegram_id=1001, user_id=1)

    req_valid = MagicMock(spec=web.Request)
    req_valid.query = {"token": session.token}
    resp_valid = await handle_login_page(req_valid)
    assert resp_valid.status == 200
    assert "Connect Zoom Account" in resp_valid.text
    assert "Cloud Browser Login" in resp_valid.text

