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
