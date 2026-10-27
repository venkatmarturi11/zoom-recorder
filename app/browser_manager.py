"""Browser lifecycle manager utilizing Playwright Chromium with media flags."""

import asyncio
import logging
from pathlib import Path
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

    async def launch_browser(
        self,
        storage_state: Optional[Dict[str, Any]] = None,
        record_video_dir: Optional[Path] = None,
    ) -> Page:
        """Launch Chromium with authorized audio/video permissions and standard viewport."""
        if self._is_running and self._page:
            return self._page

        logger.info("Starting Playwright Chromium instance with stealth anti-detection...")
        try:
            self._playwright = await async_playwright().start()

            width, height = self.settings.resolution_tuple

            # Launch arguments for stealth, media compatibility, and low RAM consumption on cloud containers
            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required",  # Auto-plays audio without clicking
                f"--window-size={width},{height}",
                "--disable-infobars",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--js-flags=--max-old-space-size=160",
                "--renderer-process-limit=1",
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
                "--disk-cache-size=10485760",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
            ]

            self._browser = await self._playwright.chromium.launch(
                headless=self.settings.browser_headless,
                args=launch_args,
                ignore_default_args=["--enable-automation"],
            )

            # Setup isolated browser context
            context_kwargs: Dict[str, Any] = {
                "viewport": {"width": width, "height": height},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "locale": "en-US",
                "timezone_id": "America/New_York",
            }

            if record_video_dir:
                record_video_dir.mkdir(parents=True, exist_ok=True)
                context_kwargs["record_video_dir"] = str(record_video_dir)
                context_kwargs["record_video_size"] = {"width": width, "height": height}
                logger.info("Enabled native Playwright tab video recording into %s", record_video_dir)

            if storage_state:
                context_kwargs["storage_state"] = storage_state
                logger.info("Injected saved authenticated session state into browser context.")

            self._context = await self._browser.new_context(**context_kwargs)

            # Mask automation attributes and emulate real browser environment
            await self._context.add_init_script("""
                // Hide webdriver property
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                
                // Add real Chrome runtime and app objects
                window.chrome = {
                    runtime: {
                        OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
                        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
                        PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                        PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
                    },
                    app: { isInstalled: false },
                    loadTimes: function() {},
                    csi: function() {}
                };

                // Languages
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

                // Plugins mock
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                    ]
                });

                // Permissions query override
                if (window.navigator && window.navigator.permissions) {
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications'
                            ? Promise.resolve({ state: Notification.permission })
                            : originalQuery ? originalQuery(parameters) : Promise.resolve({ state: 'prompt' })
                    );
                }

                // WebGL vendor/renderer spoofing (prevents headless WebGL fingerprinting)
                try {
                    const getParameter = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(parameter) {
                        if (parameter === 37445) return 'Intel Inc.';
                        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                        return getParameter.apply(this, [parameter]);
                    };
                } catch (e) {}
            """)

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
