"""Browser lifecycle manager utilizing Playwright Chromium with media flags."""

import asyncio
import logging
from typing import Any, Dict, Optional
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.config import get_settings

logger = logging.getLogger("zoom_recorder.browser")


class BrowserLaunchError(Exception):
    """Raised when Chromium fails to start or crashes."""
    pass


class BrowserManager:
    """Manages the lifecycle of the Playwright Chromium browser and page instances."""

    def __init__(self):
        self.settings = get_settings()
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._is_running: bool = False

    @property
    def is_running(self) -> bool:
        return self._is_running and (self._browser is not None)

    @property
    def page(self) -> Optional[Page]:
        return self._page

    @property
    def context(self) -> Optional[BrowserContext]:
        return self._context

    async def launch_browser(self, storage_state: Optional[Dict[str, Any]] = None) -> Page:
        """Launch Chromium with authorized audio/video permissions and standard viewport."""
        if self._is_running and self._page:
            return self._page

        logger.info("Starting Playwright Chromium instance...")
        try:
            self._playwright = await async_playwright().start()

            # Launch arguments for reliable media rendering and container compatibility
            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--disable-gpu",
                "--use-fake-ui-for-media-stream",  # Auto-grants camera/mic prompts
                "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",  # Auto-plays audio without clicking
                "--window-size=1280,720",
                "--disable-infobars",
                "--disable-blink-features=AutomationControlled",
            ]

            width, height = self.settings.resolution_tuple

            self._browser = await self._playwright.chromium.launch(
                headless=self.settings.browser_headless,
                args=launch_args,
            )

            # Setup isolated browser context
            context_kwargs = {
                "viewport": {"width": width, "height": height},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "permissions": ["microphone", "camera"],
            }

            if storage_state:
                context_kwargs["storage_state"] = storage_state
                logger.info("Injected saved authenticated session state into browser context.")

            self._context = await self._browser.new_context(**context_kwargs)
            self._page = await self._context.new_page()

            # Listen for unexpected page or browser crash
            self._page.on("crash", self._handle_crash)
            self._page.on("close", self._handle_close)

            self._is_running = True
            logger.info("Chromium initialized successfully. Viewport: %dx%d", width, height)
            return self._page

        except Exception as exc:
            logger.error("Failed to launch Chromium browser: %s", exc)
            await self.close_browser()
            raise BrowserLaunchError(f"Chromium startup failed: {exc}") from exc

    def _handle_crash(self, page: Page) -> None:
        logger.error("ALERT: Browser page crashed unexpectedly!")
        self._is_running = False

    def _handle_close(self, page: Page) -> None:
        logger.info("Browser page closed.")
        self._is_running = False

    async def get_storage_state(self) -> Optional[Dict[str, Any]]:
        """Extract cookies and local storage state for session persistence."""
        if self._context:
            try:
                return await self._context.storage_state()
            except Exception as exc:
                logger.warning("Could not export browser storage state: %s", exc)
        return None

    async def close_browser(self) -> None:
        """Safely terminate browser, context, and Playwright driver."""
        self._is_running = False
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        logger.info("Browser resources shut down safely.")
