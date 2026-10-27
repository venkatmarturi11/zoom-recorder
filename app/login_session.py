"""Secure cloud browser Zoom authentication session manager for mobile-first login."""

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from app.auth import PersistentZoomSessionManager, validate_session_state
from app.browser_manager import BrowserManager
from app.database import update_user_zoom_session
from app.notifications import NotificationService
from app.security import mask_email

logger = logging.getLogger("zoom_recorder.login_session")


@dataclass
class LoginSession:
    """Represents an active, short-lived mobile Zoom login session."""
    token: str
    telegram_id: int
    user_id: int
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 600)  # 10 minute expiry
    status: str = "PENDING"  # PENDING, AUTHENTICATING, WAITING_MFA, SUCCESS, FAILED
    error_message: Optional[str] = None
    zoom_email: Optional[str] = None
    mfa_prompt: Optional[str] = None
    mfa_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    login_task: Optional[asyncio.Task] = None

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class LoginSessionManager:
    """Singleton manager for mobile authentication sessions."""

    def __init__(self, notifier: Optional[NotificationService] = None):
        self._sessions: Dict[str, LoginSession] = {}
        self.notifier = notifier
        self.session_manager = PersistentZoomSessionManager()

    def set_notifier(self, notifier: NotificationService) -> None:
        self.notifier = notifier

    def create_session(self, telegram_id: int, user_id: int) -> LoginSession:
        """Create a fresh cryptographically secure one-time login session."""
        self.cleanup_expired()
        token = secrets.token_urlsafe(32)
        session = LoginSession(token=token, telegram_id=telegram_id, user_id=user_id)
        self._sessions[token] = session
        logger.info("Created Zoom login session for user %d (Telegram: %d)", user_id, telegram_id)
        return session

    def get_session(self, token: str) -> Optional[LoginSession]:
        """Fetch active session by token, validating expiration."""
        self.cleanup_expired()
        session = self._sessions.get(token)
        if session and session.is_expired:
            self._sessions.pop(token, None)
            return None
        return session

    def cleanup_expired(self) -> None:
        """Purge expired login sessions."""
        now = time.time()
        expired_tokens = [tok for tok, s in self._sessions.items() if now > s.expires_at]
        for tok in expired_tokens:
            s = self._sessions.pop(tok, None)
            if s and s.login_task and not s.login_task.done():
                s.login_task.cancel()

    async def start_cloud_login(self, token: str, email: str, password: str) -> None:
        """Launch background Playwright cloud browser authentication task."""
        session = self.get_session(token)
        if not session:
            raise ValueError("Authentication session is invalid or expired.")

        session.status = "AUTHENTICATING"
        session.error_message = None
        session.login_task = asyncio.create_task(
            self._execute_cloud_login(session, email.strip(), password)
        )

    async def submit_mfa_code(self, token: str, code: str) -> bool:
        """Send 2FA verification code to the active cloud browser task."""
        session = self.get_session(token)
        if not session or session.status != "WAITING_MFA":
            return False
        await session.mfa_queue.put(code.strip())
        logger.info("2FA code forwarded to cloud browser for user %d", session.user_id)
        return True

    async def import_session_state(self, token: str, raw_json_or_dict: Any) -> Tuple[bool, str]:
        """Import pre-existing browser storage state securely."""
        session = self.get_session(token)
        if not session:
            return False, "Session expired or invalid."

        try:
            if isinstance(raw_json_or_dict, str):
                storage_dict = json.loads(raw_json_or_dict)
            else:
                storage_dict = raw_json_or_dict

            valid, err = validate_session_state(storage_dict)
            if not valid:
                session.status = "FAILED"
                session.error_message = err or "Invalid session cookies."
                return False, session.error_message

            # Save encrypted
            await self.session_manager.save_session_state_async(session.user_id, storage_dict)

            # Masked email or username placeholder
            masked = "Zoom User (Imported Session)"
            await update_user_zoom_session(
                session.telegram_id,
                zoom_email=masked,
                status="CONNECTED",
            )

            session.status = "SUCCESS"
            session.zoom_email = masked

            if self.notifier:
                await self.notifier.send_custom_message(
                    chat_id=session.telegram_id,
                    message_text=(
                        "🔐 *Zoom Account Connected Successfully!*\n\n"
                        "Your session state has been verified, encrypted with AES-256, and stored.\n"
                        "Subsequent `/join` commands will automatically reuse this session."
                    ),
                )
            return True, "Session imported successfully."

        except Exception as exc:
            logger.error("Error importing session state: %s", exc)
            session.status = "FAILED"
            session.error_message = f"Failed to import: {exc}"
            return False, str(exc)

    async def _execute_cloud_login(self, session: LoginSession, email: str, password: str) -> None:
        """Stealth Playwright Chromium automation to sign in to Zoom securely."""
        browser_manager = BrowserManager()
        try:
            page = await browser_manager.launch_browser()
            logger.info("Cloud browser launched for Zoom sign-in (user %d)...", session.user_id)

            # 1. Navigate to Zoom sign in
            await page.goto("https://zoom.us/signin", timeout=45000, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)

            # Dismiss cookie dialog
            try:
                for sel in ["#onetrust-accept-btn-handler", "button:has-text('Agree and Continue')", "button:has-text('Accept All')"]:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=800):
                        await btn.click()
                        await asyncio.sleep(0.5)
            except Exception:
                pass

            # 2. Fill Email
            email_selectors = [
                "#email",
                "input#userId",
                "input[name='userId']",
                "input[type='email']",
                "#input-email",
            ]
            filled_email = False
            for sel in email_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1500):
                        await el.click()
                        await el.fill(email)
                        filled_email = True
                        break
                except Exception:
                    continue

            if not filled_email:
                raise ValueError("Could not locate email input field on Zoom signin page.")

            # Check if there is an intermediate 'Next' button (two-step login flow)
            try:
                next_btn = page.locator("button:has-text('Next'), #btnNext").first
                if await next_btn.is_visible(timeout=1000):
                    await next_btn.click()
                    await asyncio.sleep(1.5)
            except Exception:
                pass

            # 3. Fill Password
            pw_selectors = [
                "#password",
                "input#password",
                "input[name='password']",
                "input[type='password']",
            ]
            filled_pw = False
            for sel in pw_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        await el.fill(password)
                        filled_pw = True
                        break
                except Exception:
                    continue

            if not filled_pw:
                raise ValueError("Could not locate password input field on Zoom signin page.")

            # 4. Click Sign In
            submit_selectors = [
                "button#js_btn_login",
                "button[type='submit']",
                "button:has-text('Sign In')",
                "button:has-text('Sign in')",
                ".signin-btn",
            ]
            for sel in submit_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        break
                except Exception:
                    continue

            logger.info("Submitted Zoom credentials in cloud browser. Monitoring authentication outcome...")
            await asyncio.sleep(3.0)

            # 5. Monitor for outcome: MFA prompt, Success, or Error
            for _ in range(20):  # poll for up to 40 seconds
                curr_url = page.url.lower()
                content = ""
                try:
                    content = (await page.content()).lower()
                except Exception:
                    pass

                # Error checks
                if "incorrect email or password" in content or "invalid email or password" in content:
                    raise ValueError("Invalid email or password.")
                if "captcha" in content or "recaptcha" in content or "security verification" in content:
                    raise ValueError("Zoom requested automated CAPTCHA challenge.")

                # MFA / 2FA verification code check
                mfa_detected = (
                    "verification code" in content
                    or "enter the 6-digit code" in content
                    or "check your email" in content
                    or "two-factor authentication" in content
                    or "2-step verification" in content
                )
                if mfa_detected and session.status != "WAITING_MFA":
                    session.status = "WAITING_MFA"
                    session.mfa_prompt = "Zoom requires 2-factor verification. Please enter the verification code sent to your email or phone."
                    logger.info("MFA prompt detected for user %d. Waiting for mobile code input...", session.user_id)

                    # Await code from queue (up to 180s)
                    try:
                        code = await asyncio.wait_for(session.mfa_queue.get(), timeout=180.0)
                    except asyncio.TimeoutError:
                        raise TimeoutError("MFA code entry timed out after 3 minutes.")

                    # Enter MFA code
                    code_selectors = [
                        "input#code",
                        "input[name='code']",
                        "input[type='number']",
                        "input[placeholder*='code' i]",
                        "input.verification-code",
                    ]
                    code_entered = False
                    for sel in code_selectors:
                        try:
                            code_field = page.locator(sel).first
                            if await code_field.is_visible(timeout=1500):
                                await code_field.fill(code)
                                code_entered = True
                                break
                        except Exception:
                            continue

                    # Click verify/submit
                    for b_sel in ["button:has-text('Verify')", "button:has-text('Submit')", "button[type='submit']"]:
                        try:
                            v_btn = page.locator(b_sel).first
                            if await v_btn.is_visible(timeout=1000):
                                await v_btn.click()
                                break
                        except Exception:
                            continue

                    await asyncio.sleep(3.0)

                # Success check
                storage_state = await browser_manager.get_storage_state()
                if storage_state:
                    is_valid, _ = validate_session_state(storage_state)
                    if is_valid or "/profile" in curr_url or "/meeting" in curr_url:
                        logger.info("Zoom cloud authentication SUCCEEDED for user %d!", session.user_id)
                        # Save encrypted session
                        await self.session_manager.save_session_state_async(session.user_id, storage_state)

                        masked = mask_email(email)
                        await update_user_zoom_session(
                            telegram_id=session.telegram_id,
                            zoom_email=masked,
                            status="CONNECTED",
                        )

                        session.status = "SUCCESS"
                        session.zoom_email = masked

                        if self.notifier:
                            await self.notifier.send_custom_message(
                                chat_id=session.telegram_id,
                                message_text=(
                                    "✅ *Zoom Account Connected Successfully!*\n\n"
                                    f"• *Account:* `{masked}`\n"
                                    "• *Session Security:* `AES-256 Encrypted`\n\n"
                                    "Your session is verified and saved. All future `/join` recordings "
                                    "will automatically reuse this session without prompting for credentials!"
                                ),
                            )
                        return

                await asyncio.sleep(2.0)

            raise TimeoutError("Authentication verification timed out without confirming session.")

        except Exception as exc:
            logger.error("Cloud Zoom login failed for user %d: %s", session.user_id, exc)
            session.status = "FAILED"
            session.error_message = str(exc)
            if self.notifier:
                await self.notifier.send_custom_message(
                    chat_id=session.telegram_id,
                    message_text=(
                        "❌ *Zoom Authentication Failed*\n\n"
                        f"Reason: _{str(exc)}_\n\n"
                        "Please run `/login` to generate a new secure connection link and try again."
                    ),
                )
        finally:
            await browser_manager.close_browser()


# Global singleton instance
_login_manager: Optional[LoginSessionManager] = None


def get_login_session_manager(notifier: Optional[NotificationService] = None) -> LoginSessionManager:
    """Retrieve or initialize the singleton LoginSessionManager."""
    global _login_manager
    if _login_manager is None:
        _login_manager = LoginSessionManager(notifier=notifier)
    elif notifier and not _login_manager.notifier:
        _login_manager.set_notifier(notifier)
    return _login_manager
