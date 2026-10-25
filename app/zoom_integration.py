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
            await asyncio.sleep(2.0)  # Allow initial scripts to bootstrap

            # 1. Dismiss any Cookie Consent or Privacy banners
            await self._dismiss_cookie_dialogs()

            # 2. Input Participant Name if name input field is visible
            await self._fill_participant_name(display_name)

            # 3. Input Passcode if passcode field is prompted
            if passcode:
                await self._fill_passcode(passcode)

            # 4. Click the 'Join' button
            await self._click_join_button()

            # 5. Evaluate landing state
            return await self.evaluate_meeting_state(max_wait_seconds=30)

        except PlaywrightTimeoutError as exc:
            logger.error("Navigation timeout while joining Zoom meeting: %s", exc)
            raise ZoomNavigationError("Zoom web client failed to load within timeout period.") from exc
        except Exception as exc:
            logger.error("Unexpected error joining Zoom meeting: %s", exc)
            raise

    async def _dismiss_cookie_dialogs(self) -> None:
        """Dismiss common Zoom cookie/privacy dialogs."""
        cookie_selectors = [
            "#onetrust-accept-btn-handler",
            "button:has-text('Agree and Continue')",
            "button:has-text('Accept All Cookies')",
            "button:has-text('I Agree')",
        ]
        for sel in cookie_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    logger.info("Dismissed cookie/consent dialog.")
                    break
            except Exception:
                continue

    async def _fill_participant_name(self, display_name: str) -> None:
        """Locate and enter the authorized bot participant display name."""
        name_selectors = [
            "input#inputname",
            "input[name='inputname']",
            "input[placeholder*='name' i]",
            "input[aria-label*='Your Name' i]",
        ]
        for sel in name_selectors:
            try:
                field = self.page.locator(sel).first
                if await field.is_visible(timeout=3000):
                    await field.fill("")
                    await field.fill(display_name)
                    logger.info("Filled participant display name: '%s'", display_name)
                    return
            except Exception:
                continue

    async def _fill_passcode(self, passcode: str) -> None:
        """Fill passcode input if rendered."""
        passcode_selectors = [
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
        """Click the Join / Enter button on the pre-meeting web form."""
        join_btn_selectors = [
            "button#joinBtn",
            "button:has-text('Join')",
            "button.preview-join-button",
            "button[type='submit']",
        ]
        for sel in join_btn_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    logger.info("Clicked meeting join button.")
                    await asyncio.sleep(2.0)
                    return
            except Exception:
                continue

    async def evaluate_meeting_state(self, max_wait_seconds: int = 30) -> JobState:
        """Poll the page to determine current Zoom state.

        Returns one of:
        - JobState.RECORDING
        - JobState.WAITING_FOR_HOST
        - JobState.WAITING_FOR_AUTHORIZATION
        - JobState.FAILED
        """
        elapsed = 0
        step = 2

        while elapsed < max_wait_seconds:
            content = await self.page.content()
            lower_content = content.lower()

            # Check for Waiting Room
            if (
                "please wait, the meeting host will let you in soon" in lower_content
                or "waiting for the host to start this meeting" in lower_content
                or "waiting room" in lower_content
            ):
                logger.info("State detected: WAITING_FOR_HOST")
                return JobState.WAITING_FOR_HOST

            # Check for Registration / Sign-In / MFA Required
            if (
                "sign in to join" in lower_content
                or "meeting registration" in lower_content
                or "this meeting requires registration" in lower_content
                or "only authorized attendees can join" in lower_content
            ):
                logger.info("State detected: WAITING_FOR_AUTHORIZATION")
                return JobState.WAITING_FOR_AUTHORIZATION

            # Check for Meeting Ended notice
            if (
                "this meeting has been ended by the host" in lower_content
                or "the meeting has ended" in lower_content
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
