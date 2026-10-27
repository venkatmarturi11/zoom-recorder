"""Zoom Web Client automation, state detection, anti-bot evasion, and authorized meeting joining."""

import asyncio
import logging
import re
from typing import Optional, Tuple
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from app.browser_manager import BrowserManager
from app.models import JobState

logger = logging.getLogger("zoom_recorder.zoom")


class ZoomNavigationError(Exception):
    """Raised when navigation to the Zoom meeting fails or meeting link is invalid."""
    pass


class ZoomAccessError(Exception):
    """Raised when meeting requires host approval, passcode, or registration."""
    pass


class ZoomRegistrationRequiredError(ZoomAccessError):
    """Raised when a Zoom webinar or meeting strictly requires attendee registration."""

    def __init__(self, register_url: str = "", message: Optional[str] = None):
        self.register_url = register_url
        super().__init__(message or f"Host registration required at {register_url}")


class ZoomMeetingIntegrator:
    """Manages Zoom Web Client UI interactions, form filling, and meeting state transitions."""

    def __init__(self, page: Page):
        self.page = page

    async def navigate_and_join(
        self,
        join_url: str,
        display_name: str,
        passcode: Optional[str] = None,
        timeout_ms: int = 45000,
        stop_event: Optional[asyncio.Event] = None,
    ) -> JobState:
        """Navigate to Zoom Web Client, input participant name, and detect initial meeting state.

        Returns the detected JobState:
        - JobState.RECORDING (if directly in meeting)
        - JobState.WAITING_FOR_HOST (if in waiting room)
        - JobState.WAITING_FOR_AUTHORIZATION (if registration or login required)
        - JobState.PREPARING_CAPTURE (if meeting toolbar detected)
        """
        logger.info("Navigating to Zoom meeting URL: %s", join_url.split("?")[0])

        if stop_event and stop_event.is_set():
            return JobState.CANCELLED

        try:
            # 1. Navigate to Zoom meeting URL
            await self.page.goto(join_url, timeout=timeout_ms, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)  # Allow initial scripts to bootstrap

            if stop_event and stop_event.is_set():
                return JobState.CANCELLED

            # 2. Check if we landed on the desktop app launcher page and click "Join from Your Browser"
            await self._handle_browser_launcher_page()

            # 3. Check for immediate errors (invalid link, meeting ended, etc.)
            early_state = await self._check_immediate_errors()
            if early_state is not None:
                return early_state

            # 4. Wait for pre-join form or active meeting UI to render
            await self._wait_for_prejoin_or_meeting(timeout_ms=10000)

            # 5. Dismiss any Cookie Consent or Privacy banners
            await self._dismiss_cookie_dialogs()

            # 6. Dismiss Zoom's media permission dialog (pepc) and purge overlays
            await self._dismiss_media_permission_dialog()

            if stop_event and stop_event.is_set():
                return JobState.CANCELLED

            # 7. Input Participant Name if name input field is visible
            await self._fill_participant_name(display_name)

            # 8. Check if passcode field is prompted
            pwd_field_visible = await self._is_passcode_field_visible()
            if pwd_field_visible:
                if passcode:
                    await self._fill_passcode(passcode)
                else:
                    logger.warning("Meeting requires a passcode, but none was provided.")
                    raise ZoomAccessError(
                        "This Zoom meeting requires a passcode. Please provide the meeting invite link "
                        "containing the '?pwd=...' parameter, or supply the passcode (e.g., /join <meeting_id> <passcode>)."
                    )

            # 9. Accept any Terms of Service / Privacy checkboxes if present
            await self._handle_terms_checkbox()

            if stop_event and stop_event.is_set():
                return JobState.CANCELLED

            # 10. Click the 'Join' button
            await self._click_join_button()

            # 11. Evaluate landing state
            return await self.evaluate_meeting_state(
                display_name=display_name,
                passcode=passcode,
                max_wait_seconds=35,
                stop_event=stop_event,
            )

        except PlaywrightTimeoutError as exc:
            logger.error("Navigation timeout while joining Zoom meeting: %s", exc)
            raise ZoomNavigationError("Zoom web client failed to load within timeout period.") from exc
        except Exception as exc:
            logger.error("Unexpected error joining Zoom meeting: %s", exc)
            raise

    async def _handle_browser_launcher_page(self) -> bool:
        """If on the Zoom app launch page, find and click 'Join from Your Browser'."""
        try:
            # Check for direct 'Join from Your Browser' link
            browser_links = [
                "a:has-text('Join from Your Browser')",
                "a:has-text('join from your browser')",
                ".web-client-click",
                "a[href*='/wc/']",
                "#wc_default",
            ]
            for sel in browser_links:
                try:
                    link = self.page.locator(sel).first
                    if await link.is_visible(timeout=1000):
                        await link.click()
                        logger.info("Clicked 'Join from Your Browser' link (%s).", sel)
                        await asyncio.sleep(2.0)
                        return True
                except Exception:
                    continue

            # Often Zoom shows 'Launch Meeting' first; clicking or waiting triggers the web client link
            launch_btns = ["button:has-text('Launch Meeting')", "a:has-text('Launch Meeting')", "#zoom-ui-frame button"]
            for sel in launch_btns:
                try:
                    btn = self.page.locator(sel).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        await asyncio.sleep(1.5)
                        # Check again for browser link after clicking launch
                        for b_sel in browser_links:
                            b_link = self.page.locator(b_sel).first
                            if await b_link.is_visible(timeout=1500):
                                await b_link.click()
                                logger.info("Clicked 'Join from Your Browser' after Launch Meeting prompt.")
                                await asyncio.sleep(2.0)
                                return True
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("Non-fatal check on launcher page: %s", exc)

        return False

    async def _wait_for_prejoin_or_meeting(self, timeout_ms: int = 12000) -> None:
        """Wait until pre-join form, launcher, or active meeting UI appears."""
        target_selectors = [
            "#input-for-name",
            "#input-for-pwd",
            "button.preview-join-button",
            ".preview-meeting-info",
            "a:has-text('Join from Your Browser')",
            "button:has-text('Launch Meeting')",
            "#meeting-screen",
            ".meeting-app",
            ".footer-bar",
            "#foot-bar",
        ]
        if hasattr(self.page, "wait_for_selector"):
            try:
                selector_query = ", ".join(target_selectors)
                await self.page.wait_for_selector(selector_query, timeout=timeout_ms, state="attached")
                await asyncio.sleep(0.5)
            except Exception as exc:
                logger.debug("Wait for initial pre-join or meeting UI finished/timed out: %s", exc)

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
            if "this meeting link is invalid" in text or "meeting does not exist" in text or "error (3,001)" in text:
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
            raise
        except Exception as exc:
            logger.debug("Error during immediate error check (non-fatal): %s", exc)

        return None

    async def _dismiss_cookie_dialogs(self) -> None:
        """Dismiss common Zoom cookie and privacy dialogs."""
        cookie_selectors = [
            "#onetrust-accept-btn-handler",
            "button:has-text('Agree and Continue')",
            "button:has-text('Accept All Cookies')",
            "button:has-text('Accept All')",
            "button:has-text('I Agree')",
            "#cookie-dialog button",
            ".ot-sdk-button",
        ]
        for sel in cookie_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=800):
                    await btn.click()
                    logger.info("Dismissed cookie dialog '%s'.", sel)
                    await asyncio.sleep(0.3)
            except Exception:
                continue

    async def _dismiss_media_permission_dialog(self) -> None:
        """Dismiss Zoom's media permission (pepc) dialog and purge blocking overlays.

        This dialog asks "Do you want people to see you in the meeting?" with a
        "Continue without microphone and camera" button, backed by a ReactModal
        overlay with z-index: 20001 that intercepts all pointer events.
        """
        try:
            await self.page.evaluate("""() => {
                // 1. Try clicking PEPC / Continue without audio/video buttons
                const pepcBtn = document.querySelector('.pepc-permission-dialog__footer-button');
                if (pepcBtn) {
                    try { pepcBtn.click(); } catch (e) {}
                }
                const buttons = Array.from(document.querySelectorAll('button, span, a'));
                const continueBtn = buttons.find(b => {
                    const txt = (b.textContent || '').trim().toLowerCase();
                    return txt.includes('continue without') || txt.includes('join without audio');
                });
                if (continueBtn) {
                    try { continueBtn.click(); } catch (e) {}
                }

                // 2. Unconditionally purge any ReactModal overlays, backdrops, and containers
                const overlays = document.querySelectorAll(
                    '.ReactModal__Overlay, .ReactModal__Content, .ReactModalPortal'
                );
                overlays.forEach(el => el.remove());

                // 3. Remove ReactModal open class on body to restore scrolling and pointer events
                if (document.body) {
                    document.body.classList.remove('ReactModal__Body--open');
                    document.body.style.removeProperty('overflow');
                }
                return true;
            }""")
            await asyncio.sleep(0.2)
        except Exception as exc:
            logger.debug("Media permission dialog dismissal (non-critical): %s", exc)

    async def _is_passcode_field_visible(self) -> bool:
        """Check if passcode input field is currently displayed on the page."""
        passcode_selectors = [
            "#input-for-pwd",
            "input#inputpasscode",
            "input[name='inputpasscode']",
            "input[type='password']",
        ]
        for sel in passcode_selectors:
            try:
                field = self.page.locator(sel).first
                if await field.is_visible(timeout=800):
                    return True
            except Exception:
                continue
        return False

    async def _is_prejoin_visible(self) -> bool:
        """Check if Zoom pre-meeting preview / join form is visible."""
        try:
            selectors = [
                "#input-for-name",
                "#input-for-pwd",
                "button.preview-join-button",
                ".preview-join-name-input",
                ".preview-meeting-info",
            ]
            for sel in selectors:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=150):
                    return True
        except Exception:
            pass
        return False

    async def _check_passcode_error(self) -> Optional[str]:
        """Check if an incorrect passcode error message is visible."""
        try:
            err_loc = self.page.locator("#error-for-pwd, .preview-meeting-info__field-error, .zm-form-item__error").first
            if await err_loc.is_visible(timeout=150):
                txt = (await err_loc.inner_text()).strip()
                if txt:
                    return txt
        except Exception:
            pass

        try:
            body_text = ""
            if hasattr(self.page, "inner_text"):
                body_text = (await self.page.inner_text("body")).lower()
            if (
                "incorrect password" in body_text
                or "wrong meeting passcode" in body_text
                or "incorrect passcode" in body_text
                or "meeting passcode is incorrect" in body_text
            ):
                return "Incorrect meeting passcode."
        except Exception:
            pass
        return None

    async def _get_input_value(self, selector: str) -> str:
        """Safely fetch current text value of an input field."""
        try:
            loc = self.page.locator(selector).first
            if await loc.is_visible(timeout=150):
                val = await loc.input_value()
                return (val or "").strip()
        except Exception:
            pass
        return ""

    async def _dismiss_recording_disclaimer(self) -> None:
        """Dismiss Zoom's 'This meeting is being recorded' or consent modal."""
        selectors = [
            "button:has-text('Got it')",
            "button:has-text('Got It')",
            "button:has-text('Stay in Meeting')",
            "button:has-text('Agree')",
            "button:has-text('Accept')",
            ".recording-disclaimer-btn",
            ".zm-modal button:has-text('Got it')",
        ]
        for sel in selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=800):
                    await btn.click()
                    logger.info("Dismissed recording disclaimer modal via '%s'.", sel)
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue

    async def _handle_terms_checkbox(self) -> None:
        """Automatically check any required Terms of Service or Privacy Policy checkboxes."""
        try:
            await self.page.evaluate("""() => {
                const checkboxes = document.querySelectorAll('input[type="checkbox"]');
                checkboxes.forEach(cb => {
                    if (!cb.checked && cb.offsetParent !== null) {
                        cb.click();
                    }
                });
            }""")
        except Exception:
            pass

    async def _fill_participant_name(self, display_name: str) -> None:
        """Locate and enter the authorized bot participant display name.

        Uses Playwright fill and JS native descriptor setter to guarantee
        internal React state synchronization.
        """
        await self._dismiss_media_permission_dialog()
        selectors = [
            "#input-for-name",
            "input#inputname",
            "input[name='inputname']",
            "input[placeholder*='name' i]",
            "input[aria-label*='Your Name' i]",
            "input[aria-label*='name' i]",
            ".preview-join-name-input input",
            "#name-input",
        ]

        # 1. Playwright fill
        for sel in selectors:
            try:
                field = self.page.locator(sel).first
                if await field.is_visible(timeout=1000):
                    try:
                        await field.click(timeout=1000)
                    except Exception:
                        await field.click(force=True, timeout=1000)
                    await field.fill("")
                    await field.fill(display_name)
                    logger.info("Filled participant display name via Playwright '%s': '%s'", sel, display_name)
                    break
            except Exception:
                continue

        # 2. React native property setter fallback
        try:
            await self.page.evaluate("""(name) => {
                const selectors = [
                    '#input-for-name',
                    'input#inputname',
                    "input[name='inputname']",
                    "input[placeholder*='name' i]",
                    "input[aria-label*='name' i]",
                    ".preview-join-name-input input",
                    '#name-input'
                ];
                for (const sel of selectors) {
                    const inp = document.querySelector(sel);
                    if (inp) {
                        const nativeSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        )?.set;
                        if (nativeSetter) {
                            nativeSetter.call(inp, name);
                        } else {
                            inp.value = name;
                        }
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        inp.dispatchEvent(new Event('blur', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""", display_name)
        except Exception as exc:
            logger.debug("React native name setter error: %s", exc)

    async def _fill_passcode(self, passcode: str) -> None:
        """Fill passcode input if rendered."""
        await self._dismiss_media_permission_dialog()
        passcode_selectors = [
            "#input-for-pwd",
            "input#inputpasscode",
            "input[name='inputpasscode']",
            "input[type='password']",
            "input[placeholder*='passcode' i]",
            "input[aria-label*='passcode' i]",
        ]
        for sel in passcode_selectors:
            try:
                field = self.page.locator(sel).first
                if await field.is_visible(timeout=1000):
                    try:
                        await field.click(timeout=1000)
                    except Exception:
                        await field.click(force=True, timeout=1000)
                    await field.fill("")
                    await field.fill(passcode)
                    logger.info("Submitted meeting passcode via Playwright.")
                    break
            except Exception:
                continue

        # React native setter fallback for passcode
        try:
            await self.page.evaluate("""(pwd) => {
                const selectors = [
                    '#input-for-pwd',
                    'input#inputpasscode',
                    "input[name='inputpasscode']",
                    "input[type='password']",
                    "input[placeholder*='passcode' i]",
                    "input[aria-label*='passcode' i]"
                ];
                for (const sel of selectors) {
                    const inp = document.querySelector(sel);
                    if (inp) {
                        const nativeSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        )?.set;
                        if (nativeSetter) {
                            nativeSetter.call(inp, pwd);
                        } else {
                            inp.value = pwd;
                        }
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        inp.dispatchEvent(new Event('blur', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""", passcode)
        except Exception as exc:
            logger.debug("React native passcode setter error: %s", exc)

    async def _click_join_button(self) -> None:
        """Click the Join / Enter button on the pre-meeting web form."""
        await self._dismiss_media_permission_dialog()
        await asyncio.sleep(0.3)

        join_selectors = [
            "button.preview-join-button",
            "button#joinBtn",
            "button[type='submit']",
            "button:has-text('Join Meeting')",
            "button:has-text('Join')",
        ]

        # 1. Try Playwright click
        clicked = False
        for sel in join_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    is_disabled = await btn.is_disabled()
                    if is_disabled:
                        logger.warning("Join button '%s' is disabled.", sel)
                        continue
                    try:
                        await btn.click(timeout=2000)
                    except Exception:
                        await btn.click(force=True, timeout=2000)
                    logger.info("Clicked join button via Playwright: '%s'", sel)
                    clicked = True
                    break
            except Exception:
                continue

        # 2. JavaScript fallback click (immune to CSS overlays and enables button if ready)
        if not clicked:
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
                            btn.classList.remove('zm-btn--disabled', 'disabled');
                            btn.click();
                            return 'clicked: ' + sel;
                        }
                    }
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const joinBtn = buttons.find(b =>
                        b.textContent.trim().toLowerCase().startsWith('join') &&
                        b.offsetParent !== null
                    );
                    if (joinBtn) {
                        joinBtn.disabled = false;
                        joinBtn.classList.remove('zm-btn--disabled', 'disabled');
                        joinBtn.click();
                        return 'clicked_text: ' + joinBtn.textContent.trim();
                    }
                    return 'no_join_btn';
                }""")
                logger.info("Join button click via JS result: %s", result)
            except Exception as exc:
                logger.warning("JS join click failed: %s", exc)

        await asyncio.sleep(2.0)
        await self._dismiss_media_permission_dialog()

    async def evaluate_meeting_state(
        self,
        display_name: str = "",
        passcode: Optional[str] = None,
        max_wait_seconds: int = 35,
        stop_event: Optional[asyncio.Event] = None,
    ) -> JobState:
        """Poll the page to determine current Zoom state using visible text and active elements."""
        elapsed = 0
        step = 2

        while elapsed < max_wait_seconds:
            if stop_event and stop_event.is_set():
                return JobState.CANCELLED

            # Dismiss any popup disclaimers that may be blocking the meeting view
            await self._dismiss_recording_disclaimer()

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
                        clean_html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_content, flags=re.DOTALL | re.IGNORECASE)
                        text_to_check = clean_html.lower()
                except Exception:
                    pass

            # Check for invalid/expired meeting link
            if (
                "this meeting link is invalid" in text_to_check
                or "meeting does not exist" in text_to_check
                or "error (3,001)" in text_to_check
            ):
                logger.error("Meeting link is invalid or expired.")
                raise ZoomNavigationError(
                    "This Zoom meeting link is invalid or has expired (error 3,001). "
                    "Please verify the meeting is active and try again."
                )

            # Check for Webinar / Meeting Registration Required
            curr_page_url = getattr(self.page, "url", "")
            curr_url_lower = curr_page_url.lower()
            is_registration = (
                "webinar registration" in text_to_check
                or "register and join" in text_to_check
                or "this meeting requires registration" in text_to_check
                or "meeting registration" in text_to_check
                or "/webinar/register" in curr_url_lower
                or "#/registration" in curr_url_lower
                or "/register/" in curr_url_lower
            )
            if is_registration:
                logger.info("Webinar/meeting registration page detected: %s", curr_page_url)
                raise ZoomRegistrationRequiredError(
                    register_url=curr_page_url,
                    message="Meeting or webinar requires attendee registration before joining.",
                )

            # Check for Bot Protection / Sign-In Required / Login Redirect
            if (
                "automated bots aren't allowed to join this meeting" in text_to_check
                or "sign in to join" in text_to_check
                or "sign in with your zoom account" in text_to_check
                or "please sign in to join" in text_to_check
                or "only signed-in users can join" in text_to_check
                or "only authorized attendees can join" in text_to_check
                or "/signin" in curr_url_lower
                or "zoom.us/signin" in curr_url_lower
            ):
                logger.info("State detected: WAITING_FOR_AUTHORIZATION (Sign-in or host authorization required)")
                return JobState.WAITING_FOR_AUTHORIZATION

            # Check for Waiting Room in visible text
            if (
                "please wait, the meeting host will let you in soon" in text_to_check
                or "waiting for the host to start this meeting" in text_to_check
                or "the meeting host will let you in soon" in text_to_check
                or "the host will let you in shortly" in text_to_check
                or "waiting for the host to start" in text_to_check
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

            # Check for incorrect passcode error
            pwd_err = await self._check_passcode_error()
            if pwd_err:
                logger.warning("Zoom passcode rejected: %s", pwd_err)
                raise ZoomAccessError(f"Incorrect Zoom meeting passcode: {pwd_err}. Please check the passcode and try again.")

            # PRE-JOIN SCREEN GUARD:
            # While the pre-join form or join button is still visible, we are NOT yet in the meeting!
            is_prejoin = await self._is_prejoin_visible()
            if is_prejoin:
                # 1. Check if meeting requires a passcode
                if await self._is_passcode_field_visible():
                    if not passcode:
                        logger.warning("Meeting requires a passcode, but none was provided.")
                        raise ZoomAccessError(
                            "This Zoom meeting requires a passcode. Please provide the meeting invite link "
                            "containing the '?pwd=...' parameter, or supply the passcode (e.g., /join <meeting_id> <passcode>)."
                        )
                    # If passcode provided, ensure field is not blank
                    curr_pwd = await self._get_input_value("#input-for-pwd")
                    if not curr_pwd:
                        await self._fill_passcode(passcode)

                # 2. Check if name field is blank
                if display_name:
                    curr_name = await self._get_input_value("#input-for-name")
                    if not curr_name:
                        await self._fill_participant_name(display_name)

                # 3. Dismiss blocking overlays & accept terms
                await self._dismiss_media_permission_dialog()
                await self._handle_terms_checkbox()

                # 4. If stuck on pre-join after 3 seconds, retry clicking the join button
                if elapsed >= 3 and elapsed % 3 == 0:
                    logger.info("Pre-join screen still active; re-attempting join button submission...")
                    await self._click_join_button()

                # Do NOT evaluate in-meeting selectors while pre-join form is visible
                await asyncio.sleep(step)
                elapsed += step
                continue

            # Genuine In-Meeting Selectors (Pre-join screen is gone!)
            in_meeting_selectors = [
                "button:has-text('Join Audio by Computer')",
                ".join-audio-by-computer",
                "button:has-text('Computer Audio')",
                "button:has-text('Join Audio')",
                "button:has-text('Leave Meeting')",
                "button:has-text('Leave')",
                "button.leave-meeting-options__btn",
                ".footer__leave-btn",
                "#meeting-screen",
                ".meeting-app",
                "#foot-bar",
                ".footer-bar",
                ".footer-button-base",
                "button[aria-label*='mute my microphone' i]",
                "button[aria-label*='unmute my microphone' i]",
                "button[aria-label*='start video' i]",
                "button[aria-label*='stop video' i]",
                "button[aria-label*='Share Screen' i]",
                "button[aria-label*='Open participants list' i]",
                "button[aria-label*='open chat window' i]",
                ".gallery-video-container",
                ".speaker-bar-container",
                "canvas.video-canvas",
            ]

            for sel in in_meeting_selectors:
                try:
                    locator = self.page.locator(sel).first
                    if await locator.is_visible(timeout=300):
                        logger.info("Active meeting confirmed via element '%s'.", sel)
                        await self._connect_computer_audio()
                        await self._dismiss_recording_disclaimer()
                        return JobState.RECORDING
                except Exception:
                    continue

            # Check if join form has disappeared and URL indicates active session
            try:
                curr_url = getattr(self.page, "url", "")
                if "/wc/" in curr_url and elapsed >= 4:
                    if not await self._is_prejoin_visible():
                        logger.info("Pre-join form disappeared on web client URL. Confirmed active meeting.")
                        await self._connect_computer_audio()
                        await self._dismiss_recording_disclaimer()
                        return JobState.RECORDING
            except Exception:
                pass

            await asyncio.sleep(step)
            elapsed += step

        # If loop finished and pre-join is still visible, fail explicitly rather than assuming recording
        if await self._is_prejoin_visible():
            raise ZoomNavigationError("Unable to enter meeting: Stuck on pre-join preview screen. Please verify meeting passcode and access settings.")

        logger.info("State polling completed. Direct meeting confirmed.")
        return JobState.RECORDING

    async def _connect_computer_audio(self) -> None:
        """Click 'Join Audio by Computer' to route meeting audio to browser audio sink."""
        audio_selectors = [
            "button:has-text('Join Audio by Computer')",
            "button.join-audio-by-computer",
            "button:has-text('Computer Audio')",
            "button:has-text('Join Audio')",
            "button:has-text('Connect Audio')",
        ]
        for sel in audio_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=1500):
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

    async def keep_alive_poll(self) -> None:
        """Periodic maintenance call while in meeting: dismisses popups and connects audio."""
        try:
            await self._dismiss_recording_disclaimer()
            await self._connect_computer_audio()
        except Exception:
            pass


class ZoomAttendee:
    """Standalone manager to attend Zoom meetings independently."""

    def __init__(self, display_name: str = "Zoom Attendee Bot", headless: bool = True):
        self.display_name = display_name
        self.headless = headless
        self.browser_manager = BrowserManager()
        self.page: Optional[Page] = None
        self.integrator: Optional[ZoomMeetingIntegrator] = None

    async def join(self, zoom_url: str, passcode: Optional[str] = None) -> JobState:
        """Launch browser and attend the specified Zoom meeting."""
        self.page = await self.browser_manager.launch_browser()
        self.integrator = ZoomMeetingIntegrator(self.page)
        return await self.integrator.navigate_and_join(
            join_url=zoom_url,
            display_name=self.display_name,
            passcode=passcode,
        )

    async def wait_until_ended(self, max_seconds: int = 7200, poll_interval: int = 5) -> None:
        """Keep the session active until meeting ends or max_seconds expires."""
        elapsed = 0
        while elapsed < max_seconds:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            if not self.browser_manager.is_running or not self.integrator:
                break
            if await self.integrator.check_meeting_ended():
                logger.info("Meeting concluded.")
                break
            await self.integrator.keep_alive_poll()

    async def leave(self) -> None:
        """Leave meeting and shut down browser."""
        await self.browser_manager.close_browser()
