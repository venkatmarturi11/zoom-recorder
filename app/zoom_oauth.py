"""Official Zoom OAuth 2.0 Authorization Code flow, token exchange, and automatic token refresh."""

import base64
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import aiohttp

from app.config import get_settings
from app.database import update_user_zoom_session
from app.notifications import get_notification_service
from app.security import mask_email

logger = logging.getLogger("zoom_recorder.oauth")


class ZoomOAuthError(Exception):
    """Base exception for Zoom OAuth 2.0 errors."""
    pass


class ZoomOAuthCSRFError(ZoomOAuthError):
    """Raised when the OAuth state parameter is invalid or expired (CSRF protection)."""
    pass


class ZoomOAuthTokenRevokedError(ZoomOAuthError):
    """Raised when an OAuth refresh token has been revoked or expired by Zoom."""
    pass


class ZoomOAuthService:
    """Manages Zoom OAuth 2.0 authorization, token exchange, and automatic refresh cycles."""

    ZOOM_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
    ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"
    ZOOM_USER_ME_URL = "https://api.zoom.us/v2/users/me"

    # In-memory storage for active CSRF state tokens: state -> {"user_id": int, "created_at": float}
    _pending_states: Dict[str, Dict[str, Any]] = {}
    STATE_EXPIRATION_SECONDS = 900  # 15 minutes TTL

    def __init__(self):
        self.settings = get_settings()

    @classmethod
    def _cleanup_expired_states(cls) -> None:
        """Evict stale OAuth state tokens older than 15 minutes."""
        now = time.time()
        expired = [
            st for st, data in cls._pending_states.items()
            if now - data.get("created_at", 0) > cls.STATE_EXPIRATION_SECONDS
        ]
        for st in expired:
            cls._pending_states.pop(st, None)

    def is_configured(self) -> bool:
        """Check if Zoom OAuth client credentials are configured in the environment."""
        return bool(self.settings.zoom_client_id and self.settings.zoom_client_secret)

    def generate_authorization_url(self, telegram_user_id: int) -> Tuple[Optional[str], Optional[str]]:
        """Generate a secure Zoom OAuth authorization URL with a cryptographically random state parameter.

        Returns (authorization_url, state) or (None, None) if Zoom credentials are not configured.
        """
        if not self.is_configured():
            return None, None

        self._cleanup_expired_states()

        # Cryptographically secure random token (32 bytes urlsafe)
        state = secrets.token_urlsafe(32)
        self._pending_states[state] = {
            "user_id": telegram_user_id,
            "created_at": time.time(),
        }

        params = {
            "response_type": "code",
            "client_id": self.settings.zoom_client_id,
            "redirect_uri": self.settings.zoom_oauth_redirect_uri,
            "state": state,
        }
        if self.settings.zoom_oauth_scope:
            params["scope"] = self.settings.zoom_oauth_scope

        auth_url = f"{self.ZOOM_AUTHORIZE_URL}?{urlencode(params)}"
        logger.info("Generated Zoom OAuth authorization URL for Telegram user %d", telegram_user_id)
        return auth_url, state

    def validate_and_consume_state(self, state: str) -> Optional[int]:
        """Validate state token against CSRF attacks and return associated Telegram user ID.

        Enforces single-use consumption to prevent replay attacks.
        """
        self._cleanup_expired_states()
        state_data = self._pending_states.pop(state, None)
        if not state_data:
            logger.warning("OAuth CSRF check FAILED: Unknown or previously consumed state '%s'", state[:8] + "...")
            return None

        if time.time() - state_data.get("created_at", 0) > self.STATE_EXPIRATION_SECONDS:
            logger.warning("OAuth state expired for user %d", state_data.get("user_id", 0))
            return None

        return state_data.get("user_id")

    def _get_basic_auth_header(self) -> str:
        """Construct Basic Auth header using client_id and client_secret per Zoom OAuth specifications."""
        credentials = f"{self.settings.zoom_client_id}:{self.settings.zoom_client_secret}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    async def exchange_code_for_tokens(
        self,
        code: str,
        redirect_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Exchange authorization code for Zoom access and refresh tokens.

        Endpoint: POST https://zoom.us/oauth/token?grant_type=authorization_code
        """
        if not self.is_configured():
            raise ZoomOAuthError("Zoom OAuth credentials are not configured.")

        auth_header = self._get_basic_auth_header()
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or self.settings.zoom_oauth_redirect_uri,
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.ZOOM_TOKEN_URL, data=payload, headers=headers) as resp:
                    resp_json = await resp.json()
                    if resp.status != 200:
                        err_code = resp_json.get("code") or resp_json.get("error", "unknown")
                        err_desc = resp_json.get("message") or resp_json.get("error_description", "Token exchange failed")
                        logger.error("Zoom token exchange error (%d): %s - %s", resp.status, err_code, err_desc)
                        raise ZoomOAuthError(f"Zoom OAuth token exchange failed: {err_desc}")

                    access_token = resp_json.get("access_token")
                    refresh_token = resp_json.get("refresh_token")
                    expires_in = resp_json.get("expires_in", 3600)

                    if not access_token:
                        raise ZoomOAuthError("Token response missing access_token.")

                    return {
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "expires_in": expires_in,
                        "scope": resp_json.get("scope", self.settings.zoom_oauth_scope),
                    }
            except aiohttp.ClientError as exc:
                logger.error("HTTP network failure connecting to Zoom token endpoint: %s", exc)
                raise ZoomOAuthError(f"Network error communicating with Zoom: {exc}") from exc

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired Zoom access token using the stored refresh token.

        Per Zoom documentation, refresh tokens are rotated on each use. The newly
        returned refresh token MUST replace the old one.
        """
        if not self.is_configured():
            raise ZoomOAuthError("Zoom OAuth credentials are not configured.")

        auth_header = self._get_basic_auth_header()
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.ZOOM_TOKEN_URL, data=payload, headers=headers) as resp:
                    resp_json = await resp.json()
                    if resp.status in (400, 401):
                        err_name = resp_json.get("error", "")
                        logger.warning("Zoom refresh token rejected (%d): %s", resp.status, err_name)
                        raise ZoomOAuthTokenRevokedError("Zoom refresh token is expired or was revoked.")

                    if resp.status != 200:
                        err_desc = resp_json.get("message") or resp_json.get("error_description", "Token refresh failed")
                        logger.error("Zoom token refresh error (%d): %s", resp.status, err_desc)
                        raise ZoomOAuthError(f"Token refresh failed: {err_desc}")

                    new_access = resp_json.get("access_token")
                    new_refresh = resp_json.get("refresh_token") or refresh_token
                    expires_in = resp_json.get("expires_in", 3600)

                    return {
                        "access_token": new_access,
                        "refresh_token": new_refresh,
                        "expires_in": expires_in,
                        "scope": resp_json.get("scope", ""),
                    }
            except aiohttp.ClientError as exc:
                logger.error("Network error during token refresh: %s", exc)
                raise ZoomOAuthError(f"Network failure refreshing Zoom token: {exc}") from exc

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        """Fetch user identity information from Zoom REST API /v2/users/me using the authorized access token."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(self.ZOOM_USER_ME_URL, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "id": data.get("id"),
                            "account_id": data.get("account_id"),
                            "first_name": data.get("first_name", ""),
                            "last_name": data.get("last_name", ""),
                            "email": data.get("email", ""),
                        }
                    else:
                        logger.warning("Zoom /users/me returned status %d", resp.status)
                        return {}
            except Exception as exc:
                logger.debug("Failed to retrieve user profile from Zoom: %s", exc)
                return {}

    async def get_valid_token(self, telegram_user_id: int) -> Optional[str]:
        """Retrieve a guaranteed valid access token for the user, refreshing automatically if near expiry."""
        from app.auth import PersistentZoomSessionManager
        session_manager = PersistentZoomSessionManager()
        token_data = await session_manager.load_oauth_tokens_async(telegram_user_id)
        if not token_data:
            return None

        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_at = token_data.get("expires_at", 0)

        # If token is valid for at least another 5 minutes, reuse directly
        if access_token and (expires_at - time.time() > 300):
            return access_token

        # Needs refresh
        if not refresh_token:
            logger.warning("User %d has expired token and no refresh token available.", telegram_user_id)
            return None

        try:
            logger.info("Access token for user %d is near expiry; refreshing from Zoom...", telegram_user_id)
            refreshed = await self.refresh_access_token(refresh_token)

            # Update token bundle with rotated refresh token and new expiry
            token_data["access_token"] = refreshed["access_token"]
            token_data["refresh_token"] = refreshed["refresh_token"]
            token_data["expires_at"] = time.time() + refreshed["expires_in"]
            token_data["last_refreshed_at"] = time.time()

            await session_manager.save_oauth_tokens_async(telegram_user_id, token_data)
            logger.info("Successfully refreshed and rotated Zoom OAuth tokens for user %d", telegram_user_id)
            return token_data["access_token"]

        except ZoomOAuthTokenRevokedError:
            logger.warning("Zoom authorization revoked or expired for user %d", telegram_user_id)
            await update_user_zoom_session(telegram_user_id, status="EXPIRED")
            notifier = get_notification_service()
            if notifier:
                await notifier.send_custom_message(
                    chat_id=telegram_user_id,
                    message_text=(
                        "⚠️ *Zoom Authorization Expired*\n\n"
                        "Your Zoom account authorization has expired or was revoked.\n\n"
                        "Please run `/login` to reconnect your account."
                    ),
                )
            return None
        except Exception as exc:
            logger.error("Failed to refresh Zoom token for user %d: %s", telegram_user_id, exc)
            return None


_global_oauth_service: Optional[ZoomOAuthService] = None


def get_zoom_oauth_service() -> ZoomOAuthService:
    """Retrieve or create the ZoomOAuthService singleton."""
    global _global_oauth_service
    if _global_oauth_service is None:
        _global_oauth_service = ZoomOAuthService()
    return _global_oauth_service
