"""Authentication, authorization filters, and persistent Zoom session isolation."""

import functools
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
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


import time


def validate_session_state(storage_state_dict: Optional[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    """Validate that browser storage state contains unexpired Zoom authentication cookies."""
    if not storage_state_dict or not isinstance(storage_state_dict, dict):
        return False, "Empty or invalid session data."

    cookies = storage_state_dict.get("cookies", [])
    if not cookies or not isinstance(cookies, list):
        return False, "No cookies found in session state."

    now = time.time()
    zoom_cookies = []
    auth_cookie_names = {"_zm_ssid", "zpk", "zm_jwt", "_zm_ctaid", "cred", "_zm_chld", "zm_ha_session"}
    found_auth_cookies = []

    for c in cookies:
        domain = c.get("domain", "").lower()
        if "zoom.us" in domain or "zoomgov.com" in domain:
            zoom_cookies.append(c)
            name = c.get("name", "")
            if name in auth_cookie_names or name.startswith("_zm_"):
                found_auth_cookies.append(c)

    if not zoom_cookies:
        return False, "No Zoom cookies present in storage state."

    # Check expiration on auth cookies
    candidate_cookies = found_auth_cookies if found_auth_cookies else zoom_cookies
    valid_unexpired = []
    for c in candidate_cookies:
        expires = c.get("expires", -1)
        # expires == -1 or <= 0 indicates session cookie (unexpired until closed)
        if expires <= 0 or expires > now:
            valid_unexpired.append(c)

    if not valid_unexpired:
        return False, "All Zoom session cookies have expired. Re-authentication required."

    return True, None


class PersistentZoomSessionManager:
    """Manages encrypted storage, lifecycle, and cloud backup of persistent Zoom browser sessions."""

    def __init__(self):
        self.settings = get_settings()
        self.storage = EncryptedSessionStorage()
        self.session_dir = self.settings.session_storage_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _get_user_session_path(self, user_id: int) -> Path:
        """Isolated path for a user's encrypted session file."""
        return self.session_dir / f"session_user_{user_id}.enc"

    def _get_s3_storage_key(self, user_id: int) -> str:
        """S3 storage key for persistent cloud session backup."""
        return f"sessions/session_user_{user_id}.enc"

    def save_session_state(self, user_id: int, storage_state_dict: Dict[str, Any]) -> None:
        """Encrypt and securely save Playwright browser storage state."""
        raw_json = json.dumps(storage_state_dict)
        encrypted_str = self.storage.encrypt_data(raw_json)
        file_path = self._get_user_session_path(user_id)
        file_path.write_text(encrypted_str, encoding="utf-8")
        logger.info("Encrypted persistent session state saved for user %d", user_id)

    async def save_session_state_async(self, user_id: int, storage_state_dict: Dict[str, Any]) -> None:
        """Save locally and backup encrypted session to S3/Cloudflare R2 if configured."""
        self.save_session_state(user_id, storage_state_dict)
        if self.settings.storage_provider == "s3":
            try:
                from app.storage import get_storage_provider
                provider = get_storage_provider()
                file_path = self._get_user_session_path(user_id)
                s3_key = self._get_s3_storage_key(user_id)
                await provider.upload_file(file_path, s3_key)
                logger.info("Backed up encrypted Zoom session to S3: %s", s3_key)
            except Exception as exc:
                logger.warning("Could not sync session to S3 for user %d: %s", user_id, exc)

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

    async def load_session_state_async(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Load persistent browser state, restoring from S3/Cloudflare R2 if local disk was wiped."""
        file_path = self._get_user_session_path(user_id)
        if not file_path.exists() and self.settings.storage_provider == "s3":
            try:
                from app.storage import get_storage_provider
                provider = get_storage_provider()
                s3_key = self._get_s3_storage_key(user_id)
                downloaded = await provider.download_file(s3_key, file_path)
                if downloaded:
                    logger.info("Restored encrypted Zoom session from S3 for user %d", user_id)
            except Exception as exc:
                logger.debug("S3 session restore failed for user %d: %s", user_id, exc)

        return self.load_session_state(user_id)

    def clear_session_state(self, user_id: int) -> None:
        """Remove saved session state on expiration or logout."""
        file_path = self._get_user_session_path(user_id)
        if file_path.exists():
            file_path.unlink()
            logger.info("Cleared expired local session state for user %d", user_id)

    async def clear_session_state_async(self, user_id: int) -> None:
        """Clear local session and delete from S3/Cloudflare R2."""
        self.clear_session_state(user_id)
        if self.settings.storage_provider == "s3":
            try:
                from app.storage import get_storage_provider
                provider = get_storage_provider()
                s3_key = self._get_s3_storage_key(user_id)
                await provider.delete_file(s3_key)
                logger.info("Deleted encrypted Zoom session from S3: %s", s3_key)
            except Exception as exc:
                logger.debug("Failed to delete S3 session key for user %d: %s", user_id, exc)

    def validate_user_session(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """Check if user's session is present and contains unexpired cookies."""
        state = self.load_session_state(user_id)
        if not state:
            return False, "No session found."
        return validate_session_state(state)

