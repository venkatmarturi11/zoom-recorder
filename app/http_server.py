"""Lightweight async HTTP server keeping the application awake via UptimeRobot pings."""

import logging
import os
from aiohttp import web

from app.config import get_settings
from app.database import get_system_health_stats

logger = logging.getLogger("zoom_recorder.http")


async def handle_root(request: web.Request) -> web.Response:
    """Root endpoint for UptimeRobot or generic keep-alive ping monitors."""
    return web.Response(
        text="Zoom Tab Recorder is ONLINE. Keep-alive ping acknowledged.",
        content_type="text/plain",
    )


async def handle_health(request: web.Request) -> web.Response:
    """Detailed JSON health status for UptimeRobot, Docker, or Render healthchecks."""
    try:
        stats = await get_system_health_stats()
        return web.json_response({
            "status": "healthy",
            "service": "zoom-telegram-recorder",
            "active_jobs": stats.get("active_jobs", 0),
            "total_jobs": stats.get("total_jobs", 0),
            "total_recordings": stats.get("total_recordings", 0),
        })
    except Exception as exc:
        logger.error("Health check error: %s", exc)
        return web.json_response({"status": "degraded", "error": str(exc)}, status=500)


def create_health_app() -> web.Application:
    """Construct aiohttp web application for health probes."""
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ping", handle_root)
    return app


class KeepAliveServer:
    """Manages the background HTTP server for UptimeRobot keep-alive requests."""

    def __init__(self, port: int = 8080):
        self.port = port
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    async def start(self) -> None:
        """Start listening for incoming UptimeRobot keep-alive pings."""
        app = create_health_app()
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await self.site.start()
        logger.info("UptimeRobot keep-alive HTTP server listening on http://0.0.0.0:%d", self.port)

    async def stop(self) -> None:
        """Gracefully stop the keep-alive HTTP server."""
        if self.runner:
            logger.info("Stopping keep-alive HTTP server...")
            await self.runner.cleanup()
            self.runner = None
            self.site = None
