"""Application main entrypoint coordinating bot startup and graceful shutdown."""

import asyncio
import logging
import signal
import sys

from app.config import get_settings
from app.logging_config import setup_logging
from app.telegram_bot import TelegramBotApp

logger = setup_logging()


async def main() -> None:
    """Run the Zoom Tab Recorder Telegram bot."""
    settings = get_settings()
    logger.info("Starting Telegram-Controlled Zoom Tab Recorder v1.0.0")
    logger.info("Storage Provider: %s | Max Concurrency: %d", settings.storage_provider, settings.max_concurrent_jobs)

    if not settings.telegram_bot_token:
        logger.warning(
            "TELEGRAM_BOT_TOKEN is not set. Please provide your bot token via environment "
            "variable or in .env file to enable live Telegram messaging."
        )

    bot_app = TelegramBotApp()
    await bot_app.initialize()

    # Register OS signal handlers for graceful shutdown on Unix / Windows
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Received termination signal. Initiating graceful shutdown...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Signal handlers not fully supported on some Windows loop configurations
            pass

    try:
        if settings.telegram_bot_token:
            await bot_app.start()
            logger.info("Telegram Bot is running and waiting for user commands.")
            await stop_event.wait()
        else:
            logger.info("Running in local standby mode (no bot token provided). Database initialized.")
    except (KeyboardInterrupt, SystemExit):
        logger.info("Keyboard interrupt received.")
    except Exception as exc:
        logger.critical("Fatal application error in main event loop: %s", exc, exc_info=True)
    finally:
        await bot_app.stop()
        logger.info("Application shut down successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
