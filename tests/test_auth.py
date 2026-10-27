"""Automated tests for Telegram user authorization and persistent session encryption."""

import json
from unittest.mock import AsyncMock
import pytest
from app.auth import PersistentZoomSessionManager, authorized_telegram_user_only
from app.database import get_or_create_user
from app.security import EncryptedSessionStorage


@pytest.mark.asyncio
async def test_authorized_user_decorator(mock_telegram_update):
    """Test that authorized Telegram user can execute decorated handler."""
    update, context = mock_telegram_update(user_id=1001)  # 1001 is in ALLOWED_TELEGRAM_USER_IDS
    executed = False

    @authorized_telegram_user_only
    async def sample_handler(u, c):
        nonlocal executed
        executed = True
        return "OK"

    result = await sample_handler(update, context)
    assert executed is True
    assert result == "OK"
    update.effective_message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_user_blocked(mock_telegram_update):
    """Test that unauthorized Telegram user is blocked with an alert."""
    update, context = mock_telegram_update(user_id=9999)  # 9999 is NOT allowed
    executed = False

    @authorized_telegram_user_only
    async def sample_handler(u, c):
        nonlocal executed
        executed = True
        return "OK"

    result = await sample_handler(update, context)
    assert executed is False
    assert result is None
    update.effective_message.reply_text.assert_called_once()
    args, kwargs = update.effective_message.reply_text.call_args
    assert "Access Restricted" in args[0]


def test_encrypted_session_storage():
    """Verify AES-256 Fernet encryption and decryption cycle."""
    storage = EncryptedSessionStorage()
    sample_data = json.dumps({"cookies": [{"name": "z_auth", "value": "secret_token_123"}]})

    encrypted = storage.encrypt_data(sample_data)
    assert encrypted != sample_data
    assert "secret_token_123" not in encrypted

    decrypted = storage.decrypt_data(encrypted)
    assert decrypted == sample_data
    data_dict = json.loads(decrypted)
    assert data_dict["cookies"][0]["value"] == "secret_token_123"


def test_persistent_zoom_session_manager(tmp_path):
    """Verify persistent session manager saves, loads, and clears session state."""
    manager = PersistentZoomSessionManager()
    manager.session_dir = tmp_path

    user_id = 42
    state = {"cookies": [{"name": "zoom_user", "value": "authorized_session"}]}

    # Save
    manager.save_session_state(user_id, state)
    saved_file = tmp_path / f"session_user_{user_id}.enc"
    assert saved_file.exists()

    # Load
    loaded = manager.load_session_state(user_id)
    assert loaded == state

    # Clear
    manager.clear_session_state(user_id)
    assert not saved_file.exists()
    assert manager.load_session_state(user_id) is None


def test_validate_session_state():
    """Verify session cookie validation and expiration detection."""
    from app.auth import validate_session_state
    import time

    # Valid unexpired session
    valid_state = {
        "cookies": [
            {
                "name": "_zm_ssid",
                "value": "valid_token",
                "domain": ".zoom.us",
                "expires": time.time() + 3600,
            }
        ]
    }
    is_valid, err = validate_session_state(valid_state)
    assert is_valid is True
    assert err is None

    # Session with session cookie (expires == -1)
    session_cookie_state = {
        "cookies": [
            {
                "name": "_zm_ctaid",
                "value": "session_tok",
                "domain": ".zoom.us",
                "expires": -1,
            }
        ]
    }
    is_valid2, _ = validate_session_state(session_cookie_state)
    assert is_valid2 is True

    # Expired session cookies
    expired_state = {
        "cookies": [
            {
                "name": "_zm_ssid",
                "value": "expired_token",
                "domain": ".zoom.us",
                "expires": time.time() - 3600,  # 1 hour ago
            }
        ]
    }
    is_valid3, err3 = validate_session_state(expired_state)
    assert is_valid3 is False
    assert "expired" in err3.lower()

    # No Zoom cookies
    non_zoom_state = {
        "cookies": [
            {
                "name": "other_site",
                "value": "123",
                "domain": "example.com",
            }
        ]
    }
    is_valid4, err4 = validate_session_state(non_zoom_state)
    assert is_valid4 is False


def test_mask_email():
    """Verify email masking for Telegram privacy."""
    from app.security import mask_email

    assert mask_email("johndoe@example.com") == "j***e@example.com"
    assert mask_email("ab@gmail.com") == "a***@gmail.com"
    assert mask_email(None) == "Unknown"
    assert mask_email("invalid-email") == "Unknown"


@pytest.mark.asyncio
async def test_login_session_lifecycle(tmp_path):
    """Verify LoginSessionManager token generation, expiration, and state import."""
    from app.login_session import LoginSessionManager
    import time

    mgr = LoginSessionManager()
    mgr.session_manager.session_dir = tmp_path

    session = mgr.create_session(telegram_id=1001, user_id=1)
    assert session.token is not None
    assert len(session.token) > 20
    assert session.status == "PENDING"

    # Fetch active
    fetched = mgr.get_session(session.token)
    assert fetched is session

    # Import valid storage state
    valid_storage = {
        "cookies": [
            {"name": "_zm_ssid", "value": "test_tok", "domain": ".zoom.us", "expires": time.time() + 7200}
        ]
    }
    success, msg = await mgr.import_session_state(session.token, valid_storage)
    assert success is True
    assert session.status == "SUCCESS"

    # Loaded session from disk
    loaded = mgr.session_manager.load_session_state(1)
    assert loaded is not None
    assert loaded["cookies"][0]["name"] == "_zm_ssid"

