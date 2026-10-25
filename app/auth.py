"""Authentication, authorization filters, and persistent Zoom session isolation."""

import functools
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from telegram import Update
from telegram.ext import ContextTypes

from app.config import get_settings
from app.database import get_or_create_user
from app.security import EncryptedSessionStorage

logger = logging.getLogger("zoom_recorder.auth")


def authorized_telegram_user_only(func: Callable) -> Callable:
    """Decorator to enforce Telegram user allowlisting on command handlers.

    Rejects unauthorized users immediately, logs security alerts, and replies
    with an authorization required notice.
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> Any:
        if not update.effective_user:
            return None

        tg_user = update.effective_user
        db_user = await get_or_create_user(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
        )

        settings = get_settings()

        # If allowed list is specified and user is not permitted
        if settings.allowed_telegram_user_ids and not db_user.is_allowed:
            logger.warning(
                "Access DENIED: Unauthorized Telegram user %s (@%s, ID: %d) attempted '%s'",
                tg_user.full_name,
                tg_user.username,
                tg_user.id,
                update.message.text if update.message else "action",
            )
            if update.effective_message:
                await update.effective_message.reply_text(
                    "⛔ *Access Restricted*\n\n"
                    "You are not authorized to use this Zoom Recording Bot.\n"
                    f"Your Telegram ID: `{tg_user.id}`\n\n"
                    "Please provide this ID to the system administrator to request access.",
                    parse_mode="Markdown",
                )
            return None

        return await func(update, context, *args, **kwargs)

    return wrapper


class PersistentZoomSessionManager:
    """Manages encrypted storage and lifecycle of persistent Zoom browser sessions."""

    def __init__(self):
        self.settings = get_settings()
        self.storage = EncryptedSessionStorage()
        self.session_dir = self.settings.session_storage_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _get_user_session_path(self, user_id: int) -> Path:
        """Isolated path for a user's encrypted session file."""
        return self.session_dir / f"session_user_{user_id}.enc"

    def save_session_state(self, user_id: int, storage_state_dict: Dict[str, Any]) -> None:
        """Encrypt and securely save Playwright browser storage state."""
        raw_json = json.dumps(storage_state_dict)
        encrypted_str = self.storage.encrypt_data(raw_json)
        file_path = self._get_user_session_path(user_id)
        file_path.write_text(encrypted_str, encoding="utf-8")
        logger.info("Encrypted persistent session state saved for user %d", user_id)

    def load_session_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Load and decrypt persistent browser storage state if present."""
        file_path = self._get_user_session_path(user_id)
        if not file_path.exists():
            return None
        try:
            encrypted_str = file_path.read_text(encoding="utf-8")
            decrypted_str = self.storage.decrypt_data(encrypted_str)
            return json.loads(decrypted_str)
        except Exception as exc:
            logger.error("Failed to decrypt or load session for user %d: %s", user_id, exc)
            return None

    def clear_session_state(self, user_id: int) -> None:
        """Remove saved session state on expiration or logout."""
        file_path = self._get_user_session_path(user_id)
        if file_path.exists():
            file_path.unlink()
            logger.info("Cleared expired session state for user %d", user_id)
