"""Automated tests for Zoom OAuth 2.0 authorization, CSRF protection, and token lifecycle."""

import base64
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiohttp import web

from app.auth import PersistentZoomSessionManager
from app.config import get_settings
from app.database import get_or_create_user, get_user_zoom_status, update_user_zoom_session
from app.http_server import handle_oauth_callback
from app.zoom_oauth import (
    ZoomOAuthCSRFError,
    ZoomOAuthError,
    ZoomOAuthService,
    ZoomOAuthTokenRevokedError,
    get_zoom_oauth_service,
)


@pytest.fixture
def oauth_service(monkeypatch):
    """Fixture providing ZoomOAuthService with test credentials."""
    settings = get_settings()
    monkeypatch.setattr(settings, "zoom_client_id", "TEST_CLIENT_ID_123")
    monkeypatch.setattr(settings, "zoom_client_secret", "TEST_CLIENT_SECRET_XYZ")
    monkeypatch.setattr(settings, "public_app_url", "https://recorder.onrender.com")
    service = ZoomOAuthService()
    service._pending_states.clear()
    return service


def test_oauth_service_unconfigured(monkeypatch):
    """Verify service reports unconfigured and returns None when credentials are missing."""
    settings = get_settings()
    monkeypatch.setattr(settings, "zoom_client_id", None)
    monkeypatch.setattr(settings, "zoom_client_secret", None)
    service = ZoomOAuthService()
    assert service.is_configured() is False
    auth_url, state = service.generate_authorization_url(1001)
    assert auth_url is None
    assert state is None


def test_generate_authorization_url(oauth_service):
    """Verify generation of valid Zoom OAuth authorization URL with CSRF state parameter."""
    auth_url, state = oauth_service.generate_authorization_url(1001)
    assert auth_url is not None
    assert state is not None
    assert auth_url.startswith("https://zoom.us/oauth/authorize?")
    assert "client_id=TEST_CLIENT_ID_123" in auth_url
    assert "response_type=code" in auth_url
    assert f"state={state}" in auth_url
    assert "scope=user%3Aread" in auth_url
    assert "redirect_uri=https%3A%2F%2Frecorder.onrender.com%2Foauth%2Fcallback" in auth_url


def test_state_csrf_single_use_validation(oauth_service):
    """Verify CSRF state validation, single-use consumption, and expiration rules."""
    _, state = oauth_service.generate_authorization_url(1001)

    # 1. Valid state returns user_id and is consumed
    user_id = oauth_service.validate_and_consume_state(state)
    assert user_id == 1001

    # 2. Replay of consumed state fails
    replayed = oauth_service.validate_and_consume_state(state)
    assert replayed is None

    # 3. Unknown state fails
    assert oauth_service.validate_and_consume_state("fake_tampered_state") is None

    # 4. Expired state fails
    _, expired_state = oauth_service.generate_authorization_url(1002)
    oauth_service._pending_states[expired_state]["created_at"] = time.time() - 1000
    assert oauth_service.validate_and_consume_state(expired_state) is None


def make_mock_aiohttp_session(mock_resp):
    """Helper to mock aiohttp.ClientSession async context manager and HTTP methods."""
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    post_cm = MagicMock()
    post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    post_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.post = MagicMock(return_value=post_cm)

    get_cm = MagicMock()
    get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    get_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = MagicMock(return_value=get_cm)

    return mock_session


@pytest.mark.asyncio
async def test_exchange_code_for_tokens_success(oauth_service):
    """Verify authorization code is exchanged for access and refresh tokens."""
    mock_resp_json = {
        "access_token": "mock_access_token_123",
        "token_type": "bearer",
        "refresh_token": "mock_refresh_token_abc",
        "expires_in": 3600,
        "scope": "user:read",
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_resp_json)
    mock_session = make_mock_aiohttp_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        tokens = await oauth_service.exchange_code_for_tokens("mock_auth_code")
        assert tokens["access_token"] == "mock_access_token_123"
        assert tokens["refresh_token"] == "mock_refresh_token_abc"
        assert tokens["expires_in"] == 3600
        assert tokens["scope"] == "user:read"

        # Verify Basic Auth credentials header
        _, kwargs = mock_session.post.call_args
        auth_header = kwargs["headers"]["Authorization"]
        expected_creds = base64.b64encode(b"TEST_CLIENT_ID_123:TEST_CLIENT_SECRET_XYZ").decode()
        assert auth_header == f"Basic {expected_creds}"
        assert kwargs["data"]["grant_type"] == "authorization_code"
        assert kwargs["data"]["code"] == "mock_auth_code"


@pytest.mark.asyncio
async def test_exchange_code_for_tokens_failure(oauth_service):
    """Verify token exchange error raises ZoomOAuthError."""
    mock_resp = AsyncMock()
    mock_resp.status = 400
    mock_resp.json = AsyncMock(return_value={"error": "invalid_grant", "message": "Code is expired"})
    mock_session = make_mock_aiohttp_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(ZoomOAuthError, match="Code is expired"):
            await oauth_service.exchange_code_for_tokens("expired_code")


@pytest.mark.asyncio
async def test_refresh_token_rotation(oauth_service):
    """Verify token refresh flow receives rotated refresh token per Zoom documentation."""
    mock_resp_json = {
        "access_token": "new_refreshed_access_token",
        "token_type": "bearer",
        "refresh_token": "new_rotated_refresh_token",
        "expires_in": 3600,
        "scope": "user:read",
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_resp_json)
    mock_session = make_mock_aiohttp_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        refreshed = await oauth_service.refresh_access_token("current_refresh_token")
        assert refreshed["access_token"] == "new_refreshed_access_token"
        assert refreshed["refresh_token"] == "new_rotated_refresh_token"


@pytest.mark.asyncio
async def test_refresh_token_revoked_error(oauth_service):
    """Verify expired or revoked refresh token raises ZoomOAuthTokenRevokedError."""
    mock_resp = AsyncMock()
    mock_resp.status = 401
    mock_resp.json = AsyncMock(return_value={"error": "invalid_grant", "message": "Token revoked"})
    mock_session = make_mock_aiohttp_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(ZoomOAuthTokenRevokedError):
            await oauth_service.refresh_access_token("revoked_refresh_token")


@pytest.mark.asyncio
async def test_get_user_profile(oauth_service):
    """Verify retrieval of user profile identity from Zoom REST API /v2/users/me."""
    mock_profile_json = {
        "id": "u123456789",
        "first_name": "Antigravity",
        "last_name": "User",
        "email": "antigravity.user@example.com",
        "account_id": "acc_987654321",
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_profile_json)
    mock_session = make_mock_aiohttp_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        profile = await oauth_service.get_user_profile("valid_access_token")
        assert profile["id"] == "u123456789"
        assert profile["email"] == "antigravity.user@example.com"
        assert profile["first_name"] == "Antigravity"
        assert profile["account_id"] == "acc_987654321"

    with patch("aiohttp.ClientSession", return_value=mock_session):
        profile = await oauth_service.get_user_profile("valid_access_token")
        assert profile["id"] == "u123456789"
        assert profile["email"] == "antigravity.user@example.com"
        assert profile["first_name"] == "Antigravity"
        assert profile["account_id"] == "acc_987654321"


def test_encrypted_oauth_token_storage(tmp_path, monkeypatch):
    """Verify OAuth tokens are encrypted at rest with AES-256 Fernet."""
    settings = get_settings()
    monkeypatch.setattr(settings, "session_storage_dir", tmp_path)
    monkeypatch.setattr(settings, "app_secret_key", "test_super_secret_key_1234567890")

    session_mgr = PersistentZoomSessionManager()
    token_bundle = {
        "access_token": "secret_access_token_abc",
        "refresh_token": "secret_refresh_token_xyz",
        "expires_at": time.time() + 3600,
        "user_profile": {"email": "user@example.com"},
    }

    # 1. Save tokens
    session_mgr.save_oauth_tokens(user_id=1001, token_data=token_bundle)

    # 2. Verify raw file is encrypted (contains no plaintext token or email)
    enc_file = session_mgr._get_user_oauth_path(1001)
    assert enc_file.exists()
    raw_content = enc_file.read_text(encoding="utf-8")
    assert "secret_access_token_abc" not in raw_content
    assert "secret_refresh_token_xyz" not in raw_content
    assert "user@example.com" not in raw_content

    # 3. Decrypt and load
    loaded = session_mgr.load_oauth_tokens(1001)
    assert loaded is not None
    assert loaded["access_token"] == "secret_access_token_abc"
    assert loaded["refresh_token"] == "secret_refresh_token_xyz"
    assert loaded["user_profile"]["email"] == "user@example.com"

    # 4. Clear tokens
    session_mgr.clear_oauth_tokens(1001)
    assert not enc_file.exists()
    assert session_mgr.load_oauth_tokens(1001) is None


@pytest.mark.asyncio
async def test_oauth_callback_endpoint_success(oauth_service, tmp_path, monkeypatch):
    """Verify /oauth/callback endpoint handles authorization code exchange and persists tokens."""
    settings = get_settings()
    monkeypatch.setattr(settings, "session_storage_dir", tmp_path)

    # 1. Generate active state
    _, state = oauth_service.generate_authorization_url(1001)

    # 2. Mock token exchange and user profile
    token_response = {
        "access_token": "zoom_at_12345",
        "refresh_token": "zoom_rt_67890",
        "expires_in": 3600,
        "scope": "user:read",
    }
    user_profile = {
        "id": "zm_user_1",
        "first_name": "Test",
        "last_name": "Developer",
        "email": "developer@example.com",
        "account_id": "zm_acc_1",
    }

    with patch.object(oauth_service, "exchange_code_for_tokens", AsyncMock(return_value=token_response)), \
         patch.object(oauth_service, "get_user_profile", AsyncMock(return_value=user_profile)), \
         patch("app.http_server.get_zoom_oauth_service", return_value=oauth_service):

        req = MagicMock(spec=web.Request)
        req.query = {"code": "valid_zoom_code", "state": state}

        resp = await handle_oauth_callback(req)
        assert resp.status == 200
        assert "Zoom Account Connected" in resp.text
        assert "d***r@example.com" in resp.text  # Masked email

        # Verify DB updated
        status = await get_user_zoom_status(1001)
        assert status["status"] == "CONNECTED"
        assert status["zoom_email"] == "d***r@example.com"

        # Verify tokens saved securely
        session_mgr = PersistentZoomSessionManager()
        saved = session_mgr.load_oauth_tokens(1001)
        assert saved is not None
        assert saved["access_token"] == "zoom_at_12345"
        assert saved["refresh_token"] == "zoom_rt_67890"


@pytest.mark.asyncio
async def test_oauth_callback_endpoint_csrf_failure(oauth_service):
    """Verify /oauth/callback rejects invalid or replayed state with 403 Forbidden."""
    with patch("app.http_server.get_zoom_oauth_service", return_value=oauth_service):
        req = MagicMock(spec=web.Request)
        req.query = {"code": "valid_code", "state": "invalid_or_forged_state"}

        resp = await handle_oauth_callback(req)
        assert resp.status == 403
        assert "Security Verification Failed" in resp.text


@pytest.mark.asyncio
async def test_oauth_callback_user_denied():
    """Verify /oauth/callback gracefully handles user declining authorization on mobile."""
    req = MagicMock(spec=web.Request)
    req.query = {"error": "access_denied", "error_description": "The user denied the request"}

    resp = await handle_oauth_callback(req)
    assert resp.status == 200
    assert "Authorization Cancelled" in resp.text


@pytest.mark.asyncio
async def test_cmd_login_unconfigured(mock_telegram_update, monkeypatch):
    """Verify /login outputs helpful setup instructions when Zoom credentials are not yet configured."""
    from app.commands import cmd_login
    settings = get_settings()
    monkeypatch.setattr(settings, "zoom_client_id", None)
    monkeypatch.setattr(settings, "zoom_client_secret", None)

    update, context = mock_telegram_update(user_id=1001)
    await cmd_login(update, context)

    update.effective_message.reply_text.assert_called_once()
    args, _ = update.effective_message.reply_text.call_args
    reply_text = args[0] if args else update.effective_message.reply_text.call_args[1]["text"]

    assert "Zoom OAuth 2.0 Configuration Required" in reply_text
    assert "ZOOM_CLIENT_ID" in reply_text
    assert "ZOOM_CLIENT_SECRET" in reply_text
    assert "user:read" in reply_text


@pytest.mark.asyncio
async def test_cmd_login_configured_oauth(mock_telegram_update, oauth_service, monkeypatch):
    """Verify /login generates an official Zoom OAuth 2.0 URL when credentials are configured."""
    from app.commands import cmd_login
    with patch("app.commands.get_zoom_oauth_service", return_value=oauth_service):
        update, context = mock_telegram_update(user_id=1001)
        await cmd_login(update, context)

        update.effective_message.reply_text.assert_called_once()
        _, kwargs = update.effective_message.reply_text.call_args
        reply_text = kwargs.get("text", "")
        markup = kwargs.get("reply_markup")

        assert "Official Zoom OAuth 2.0 Login" in reply_text
        assert "https://zoom.us/oauth/authorize" in reply_text
        assert "user:read" in reply_text
        assert markup is not None
        btn = markup.inline_keyboard[0][0]
        assert "Connect with Zoom (OAuth 2.0)" in btn.text
        assert "https://zoom.us/oauth/authorize" in btn.url


@pytest.mark.asyncio
async def test_cmd_account_and_logout_oauth(mock_telegram_update, oauth_service, tmp_path, monkeypatch):
    """Verify /account reports OAuth token health and /logout wipes encrypted tokens."""
    from app.commands import cmd_account, cmd_logout
    settings = get_settings()
    monkeypatch.setattr(settings, "session_storage_dir", tmp_path)

    # 1. Store test OAuth tokens
    session_mgr = PersistentZoomSessionManager()
    session_mgr.save_oauth_tokens(
        user_id=1001,
        token_data={
            "access_token": "valid_token",
            "refresh_token": "valid_rt",
            "expires_at": time.time() + 3600,
            "user_profile": {
                "first_name": "Alex",
                "last_name": "Smith",
                "email": "alex.smith@example.com",
                "account_id": "ACC-12345",
            },
        },
    )
    await update_user_zoom_session(1001, zoom_email="a***h@example.com", status="CONNECTED")

    with patch("app.commands.get_zoom_oauth_service", return_value=oauth_service), \
         patch.object(oauth_service, "get_valid_token", AsyncMock(return_value="valid_token")):

        update, context = mock_telegram_update(user_id=1001)

        # Run /account
        await cmd_account(update, context)
        args, kwargs = update.effective_message.reply_text.call_args
        acc_text = args[0] if args else kwargs.get("text", "")
        assert "CONNECTED" in acc_text
        assert "Alex Smith" in acc_text
        assert "ACC-12345" in acc_text
        assert "Official Zoom OAuth 2.0 (user:read)" in acc_text

        # Run /logout
        update_out, context_out = mock_telegram_update(user_id=1001)
        await cmd_logout(update_out, context_out)

        args_out, kwargs_out = update_out.effective_message.reply_text.call_args
        logout_text = args_out[0] if args_out else kwargs_out.get("text", "")
        assert "Zoom Account Disconnected" in logout_text
        assert session_mgr.load_oauth_tokens(1001) is None
