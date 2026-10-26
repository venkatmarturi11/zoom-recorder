"""Zoom Web Client automation, state detection, and authorized meeting joining."""

import asyncio
import logging
from typing import Optional, Tuple
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from app.models import JobState

logger = logging.getLogger("zoom_recorder.zoom")


class ZoomNavigationError(Exception):
    """Raised when navigation to the Zoom meeting fails."""
    pass


class ZoomAccessError(Exception):
    """Raised when meeting requires host approval, passcode, or registration."""
    pass


class ZoomMeetingIntegrator:
    """Manages Zoom Web Client UI interactions and meeting state transitions."""

    def __init__(self, page: Page):
        self.page = page

    async def navigate_and_join(
        self,
        join_url: str,
        display_name: str,
        passcode: Optional[str] = None,
        timeout_ms: int = 45000,
    ) -> JobState:
        """Navigate to Zoom Web Client, input participant name, and detect initial meeting state.

        Returns the detected JobState:
        - JobState.RECORDING (if directly in meeting)
        - JobState.WAITING_FOR_HOST (if in waiting room)
        - JobState.WAITING_FOR_AUTHORIZATION (if registration or login required)
        """
        logger.info("Navigating to Zoom meeting URL: %s", join_url.split("?")[0])

        try:
            await self.page.goto(join_url, timeout=timeout_ms, wait_until="domcontentloaded")
            await asyncio.sleep(3.0)  # Allow initial scripts to bootstrap

            # 0. Check for immediate errors (invalid link, meeting ended, etc.)
            early_state = await self._check_immediate_errors()
            if early_state is not None:
                return early_state

            # 1. Dismiss any Cookie Consent or Privacy banners
            await self._dismiss_cookie_dialogs()

            # 2. Dismiss Zoom's media permission dialog (pepc)
            await self._dismiss_media_permission_dialog()

            # 3. Input Participant Name if name input field is visible
            await self._fill_participant_name(display_name)

            # 4. Input Passcode if passcode field is prompted
            if passcode:
                await self._fill_passcode(passcode)

            # 5. Click the 'Join' button
            await self._click_join_button()

            # 6. Evaluate landing state
            return await self.evaluate_meeting_state(max_wait_seconds=30)

        except PlaywrightTimeoutError as exc:
            logger.error("Navigation timeout while joining Zoom meeting: %s", exc)
            raise ZoomNavigationError("Zoom web client failed to load within timeout period.") from exc
        except Exception as exc:
            logger.error("Unexpected error joining Zoom meeting: %s", exc)
            raise

    async def _check_immediate_errors(self) -> Optional[JobState]:
        """Check for immediate error states like invalid meeting links."""
        try:
            title = await self.page.title()
            text = ""
            try:
                text = (await self.page.inner_text("body")).lower()
            except Exception:
                pass

            # Check for invalid/expired meeting link
            if "error" in title.lower() or "this meeting link is invalid" in text:
                logger.error("Meeting link is invalid or expired. Page title: %s", title)
                raise ZoomNavigationError(
                    "This Zoom meeting link is invalid or has expired (error 3,001). "
                    "Please check that the meeting is active and try again with a valid link."
                )

            # Check for meeting that hasn't started
            if "this meeting has not started" in text:
                logger.info("Meeting has not started yet.")
                return JobState.WAITING_FOR_HOST

        except ZoomNavigationError:
            raise  # Re-raise navigation errors
        except Exception as exc:
            logger.debug("Error during immediate error check (non-fatal): %s", exc)

        return None

    async def _dismiss_cookie_dialogs(self) -> None:
        """Dismiss common Zoom cookie and privacy dialogs."""
        cookie_selectors = [
            "#onetrust-accept-btn-handler",
            "button:has-text('Agree and Continue')",
            "button:has-text('Accept All Cookies')",
            "button:has-text('I Agree')",
        ]
        for sel in cookie_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    logger.info("Dismissed cookie dialog '%s'.", sel)
                    await asyncio.sleep(0.5)
            except Exception:
                continue

    async def _dismiss_media_permission_dialog(self) -> None:
        """Dismiss Zoom's media permission (pepc) dialog via JavaScript.

        This dialog asks "Do you want people to hear you in the meeting?" with a
        "Continue without microphone and camera" button. Since we don't use
        --use-fake-ui-for-media-stream (which triggers bot detection), we must
        dismiss this dialog programmatically.
        """
        try:
            result = await self.page.evaluate("""() => {
                // Try the specific pepc button first
                const pepcBtn = document.querySelector('.pepc-permission-dialog__footer-button');
                if (pepcBtn) {
                    pepcBtn.click();
                    return 'dismissed_pepc';
                }
                // Try text-based fallback
                const buttons = Array.from(document.querySelectorAll('button'));
                const continueBtn = buttons.find(b =>
                    b.textContent.toLowerCase().includes('continue without')
                );
                if (continueBtn) {
                    continueBtn.click();
                    return 'dismissed_continue';
                }
                return 'no_dialog';
            }""")
            if result != 'no_dialog':
                logger.info("Dismissed media permission dialog: %s", result)
                await asyncio.sleep(1.0)

                # Remove any lingering ReactModal overlay that blocks clicks
                await self.page.evaluate("""() => {
                    const overlays = document.querySelectorAll(
                        '.ReactModal__Overlay, .ReactModal__Content'
                    );
                    overlays.forEach(el => el.remove());
                }""")
                await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug("Media permission dialog dismissal (non-critical): %s", exc)

    async def _fill_participant_name(self, display_name: str) -> None:
        """Locate and enter the authorized bot participant display name.

        Uses JavaScript to set the value on React-controlled inputs to ensure
        the internal React state is updated, not just the DOM value.
        """
        try:
            result = await self.page.evaluate("""(name) => {
                const selectors = [
                    '#input-for-name',
                    'input#inputname',
                    "input[name='inputname']",
                    "input[placeholder*='name' i]",
                    "input[aria-label*='Your Name' i]"
                ];
                for (const sel of selectors) {
                    const inp = document.querySelector(sel);
                    if (inp && inp.offsetParent !== null) {
                        // Use native setter to trigger React's synthetic event system
                        const nativeSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        nativeSetter.call(inp, name);
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        return 'filled: ' + sel;
                    }
                }
                return 'no_input_found';
            }""", display_name)
            if 'filled' in result:
                logger.info("Filled participant display name: '%s' via %s", display_name, result)
            else:
                logger.debug("No name input field found on page (may not be needed).")
        except Exception as exc:
            logger.debug("Name fill via JS failed, falling back to Playwright: %s", exc)
            # Fallback to Playwright locator
            for sel in ["#input-for-name", "input#inputname", "input[placeholder*='name' i]"]:
                try:
                    field = self.page.locator(sel).first
                    if await field.is_visible(timeout=2000):
                        await field.fill("")
                        await field.fill(display_name)
                        logger.info("Filled participant name via Playwright: '%s'", display_name)
                        return
                except Exception:
                    continue

    async def _fill_passcode(self, passcode: str) -> None:
        """Fill passcode input if rendered."""
        passcode_selectors = [
            "#input-for-pwd",
            "input#inputpasscode",
            "input[name='inputpasscode']",
            "input[type='password']",
            "input[placeholder*='passcode' i]",
        ]
        for sel in passcode_selectors:
            try:
                field = self.page.locator(sel).first
                if await field.is_visible(timeout=2000):
                    await field.fill(passcode)
                    logger.info("Submitted meeting passcode.")
                    return
            except Exception:
                continue

    async def _click_join_button(self) -> None:
        """Click the Join / Enter button on the pre-meeting web form.

        Uses JavaScript click to bypass any remaining overlay elements,
        then waits and dismisses any follow-up media permission dialogs.
        """
        try:
            result = await self.page.evaluate("""() => {
                const selectors = [
                    'button.preview-join-button',
                    'button#joinBtn',
                    "button[type='submit']"
                ];
                for (const sel of selectors) {
                    const btn = document.querySelector(sel);
                    if (btn && btn.offsetParent !== null) {
                        btn.disabled = false;
                        btn.click();
                        return 'clicked: ' + sel;
                    }
                }
                // Fallback: find any button with "Join" text
                const buttons = Array.from(document.querySelectorAll('button'));
                const joinBtn = buttons.find(b =>
                    b.textContent.trim().toLowerCase().startsWith('join') &&
                    b.offsetParent !== null
                );
                if (joinBtn) {
                    joinBtn.click();
                    return 'clicked_text: ' + joinBtn.textContent.trim();
                }
                return 'no_join_btn';
            }""")
            logger.info("Join button click result: %s", result)
        except Exception as exc:
            logger.warning("JS join click failed, trying Playwright: %s", exc)
            # Fallback to Playwright
            for sel in ["button.preview-join-button", "button#joinBtn", "button:has-text('Join')"]:
                try:
                    btn = self.page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        try:
                            await btn.click(timeout=3000)
                        except Exception:
                            await btn.click(force=True)
                        logger.info("Clicked join button via Playwright: '%s'", sel)
                        break
                except Exception:
                    continue

        await asyncio.sleep(3.0)

        # Dismiss any follow-up media permission dialog that appears after clicking Join
        await self._dismiss_media_permission_dialog()

        await asyncio.sleep(2.0)

    async def evaluate_meeting_state(self, max_wait_seconds: int = 30) -> JobState:
        """Poll the page to determine current Zoom state using visible text and active elements.

        Returns one of:
        - JobState.RECORDING
        - JobState.WAITING_FOR_HOST
        - JobState.WAITING_FOR_AUTHORIZATION
        - JobState.FAILED
        """
        elapsed = 0
        step = 2

        while elapsed < max_wait_seconds:
            text_to_check = ""
            if hasattr(self.page, "inner_text"):
                try:
                    res = await self.page.inner_text("body")
                    if isinstance(res, str) and res.strip():
                        text_to_check = res.lower()
                except Exception:
                    pass

            if not text_to_check and hasattr(self.page, "content"):
                try:
                    raw_content = await self.page.content()
                    if isinstance(raw_content, str):
                        import re
                        clean_html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_content, flags=re.DOTALL | re.IGNORECASE)
                        text_to_check = clean_html.lower()
                except Exception:
                    pass

            # Check for invalid/expired meeting link
            if (
                "this meeting link is invalid" in text_to_check
                or "meeting does not exist" in text_to_check
            ):
                logger.error("Meeting link is invalid or expired.")
                raise ZoomNavigationError(
                    "This Zoom meeting link is invalid or has expired. "
                    "Please verify the meeting is active and try again."
                )

            # Check for Bot Protection / Sign-In / Registration Required
            if (
                "automated bots aren't allowed to join this meeting" in text_to_check
                or "sign in to join" in text_to_check
                or "this meeting requires registration" in text_to_check
                or "only authorized attendees can join" in text_to_check
                or "meeting registration" in text_to_check
            ):
                logger.info("State detected: WAITING_FOR_AUTHORIZATION (Sign-in or bot check required)")
                return JobState.WAITING_FOR_AUTHORIZATION

            # Check for Waiting Room in visible text
            if (
                "please wait, the meeting host will let you in soon" in text_to_check
                or "waiting for the host to start this meeting" in text_to_check
                or "the meeting host will let you in soon" in text_to_check
            ):
                logger.info("State detected: WAITING_FOR_HOST")
                return JobState.WAITING_FOR_HOST

            # Check for Meeting Ended notice
            if (
                "this meeting has been ended by the host" in text_to_check
                or "the meeting has ended" in text_to_check
            ):
                logger.info("Meeting has ended.")
                return JobState.COMPLETED

            # Check for Active Meeting indicators
            # E.g. Join Audio dialog, toolbar, or video gallery canvas
            in_meeting_selectors = [
                "button:has-text('Join Audio by Computer')",
                ".join-audio-by-computer",
                "#meeting-screen",
                ".meeting-app",
                ".gallery-video-container",
                ".speaker-bar-container",
                "canvas.video-canvas",
                "#footer",
                "button[aria-label*='Mute' i]",
                "button[aria-label*='Audio' i]",
            ]

            for sel in in_meeting_selectors:
                try:
                    locator = self.page.locator(sel).first
                    if await locator.is_visible(timeout=500):
                        logger.info("Active meeting confirmed via element '%s'.", sel)
                        # Connect computer audio if prompted
                        await self._connect_computer_audio()
                        return JobState.RECORDING
                except Exception:
                    continue

            await asyncio.sleep(step)
            elapsed += step

        # If after timeout no specific screen was found, assume meeting is ready if toolbar exists
        logger.info("State polling completed. Defaulting to PREPARING_CAPTURE.")
        return JobState.PREPARING_CAPTURE

    async def _connect_computer_audio(self) -> None:
        """Click 'Join Audio by Computer' to route meeting audio to browser audio sink."""
        audio_selectors = [
            "button:has-text('Join Audio by Computer')",
            "button.join-audio-by-computer",
            "button:has-text('Computer Audio')",
        ]
        for sel in audio_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    logger.info("Clicked 'Join Audio by Computer'. Meeting audio loop connected.")
                    return
            except Exception:
                continue

    async def check_meeting_ended(self) -> bool:
        """Check if meeting has terminated."""
        try:
            content = (await self.page.content()).lower()
            return (
                "this meeting has been ended by the host" in content
                or "the meeting has ended" in content
                or "you have left the meeting" in content
            )
        except Exception:
            return True
