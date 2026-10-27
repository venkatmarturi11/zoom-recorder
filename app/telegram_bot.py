"""Telegram Bot application lifecycle, handler registration, and runtime execution."""

import asyncio
import logging
from typing import Optional
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.commands import (
    cmd_cancel,
    cmd_download,
    cmd_health,
    cmd_help,
    cmd_join,
    cmd_login,
    cmd_logout,
    cmd_recordings,
    cmd_setname,
    cmd_start,
    cmd_status,
    cmd_stop,
    cmd_account,
    handle_callback_query,
    handle_message,
    set_job_manager,
)
from app.config import get_settings
from app.database import init_db
from app.http_server import KeepAliveServer
from app.job_manager import JobManager
from app.notifications import NotificationService

logger = logging.getLogger("zoom_recorder.bot")


class TelegramBotApp:
    """Manages the Telegram Bot Application lifecycle and event dispatching."""

    def __init__(self):
        self.settings = get_settings()
        self.app: Optional[Application] = None
        self.job_manager: Optional[JobManager] = None
        self.notifier: Optional[NotificationService] = None
        self.http_server: Optional[KeepAliveServer] = None

    async def initialize(self) -> Application:
        """Initialize database, job manager, and Telegram Application."""
        logger.info("Initializing database schema...")
        await init_db()

        self.notifier = NotificationService()
        self.job_manager = JobManager(notifier=self.notifier)
        await self.job_manager.startup()
        set_job_manager(self.job_manager)

        token = self.settings.telegram_bot_token
        if not token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN is not configured! Bot cannot connect to Telegram servers. "
                "Set TELEGRAM_BOT_TOKEN in .env or environment variables."
            )
            # Create a mock application for local development/testing without real token
            token = "000000000:MOCK_TOKEN_FOR_TESTING_ONLY_ABCDEFGHIJK"

        builder = Application.builder().token(token)
        self.app = builder.build()

        self.notifier.set_bot(self.app.bot)
        self._register_handlers()

        return self.app

    def _register_handlers(self) -> None:
        """Register all required command and callback handlers."""
        if not self.app:
            return

        handlers = [
            CommandHandler("start", cmd_start),
            CommandHandler("login", cmd_login),
            CommandHandler("account", cmd_account),
            CommandHandler("logout", cmd_logout),
            CommandHandler("setname", cmd_setname),
            CommandHandler("name", cmd_setname),
            CommandHandler("help", cmd_help),
            CommandHandler("join", cmd_join),
            CommandHandler("status", cmd_status),
            CommandHandler("stop", cmd_stop),
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("recordings", cmd_recordings),
            CommandHandler("download", cmd_download),
            CommandHandler("health", cmd_health),
            CallbackQueryHandler(handle_callback_query),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
        ]

        for handler in handlers:
            self.app.add_handler(handler)

        self.app.add_error_handler(self._on_error)

        logger.info("Successfully registered %d Telegram handlers with global error handling.", len(handlers))

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Global error handler to capture, log, and gracefully inform users of failures."""
        error = context.error
        logger.error("Unhandled Telegram error: %s", error, exc_info=error)
        if isinstance(update, Update) and update.effective_message:
            try:
                err_text = str(error)
                if "wrong http url" in err_text.lower():
                    await update.effective_message.reply_text(
                        "⚠️ *URL Notice:* Unable to attach direct button for non-HTTPS URL. "
                        "Please use the direct link provided in the message.",
                        parse_mode="Markdown",
                    )
                elif "conflict" in err_text.lower():
                    pass  # Multi-instance polling conflict
                else:
                    await update.effective_message.reply_text(
                        f"⚠️ An error occurred while processing your request: `{err_text[:200]}`",
                        parse_mode="Markdown",
                    )
            except Exception:
                pass

    async def start(self) -> None:
        """Start the bot in polling or webhook mode, alongside the UptimeRobot keep-alive HTTP server."""
        if not self.app:
            await self.initialize()

        await self.app.initialize()
        await self.app.start()

        # Register Telegram Commands Menu (the blue "Menu" button next to input bar)
        try:
            from telegram import BotCommand
            commands = [
                BotCommand("join", "Join and record a Zoom meeting"),
                BotCommand("status", "Check current recording status"),
                BotCommand("stop", "Stop active recording & upload MP4"),
                BotCommand("login", "One-time secure Zoom login"),
                BotCommand("account", "Check Zoom connection & session"),
                BotCommand("recordings", "View past recordings & download links"),
                BotCommand("setname", "Change bot display name in meetings"),
                BotCommand("help", "Help guide & instructions"),
                BotCommand("logout", "Disconnect saved Zoom session"),
                BotCommand("cancel", "Cancel queued recording job"),
                BotCommand("health", "System health report"),
            ]
            await self.app.bot.set_my_commands(commands)
            logger.info("Configured Telegram Bot Command Menu successfully.")
        except Exception as exc:
            logger.debug("Could not set Telegram bot commands menu (non-critical): %s", exc)

        if self.settings.webhook_url:
            logger.info("Starting Telegram Bot in Webhook mode on port %d...", self.settings.effective_port)
            await self.app.updater.start_webhook(
                listen="0.0.0.0",
                port=self.settings.effective_port,
                url_path="webhook",
                webhook_url=f"{self.settings.webhook_url.rstrip('/')}/webhook",
            )
        else:
            # Start keep-alive HTTP server for UptimeRobot pings and Render port binding
            port = self.settings.effective_port
            try:
                self.http_server = KeepAliveServer(port=port)
                await self.http_server.start()
            except Exception as exc:
                logger.warning("Could not start keep-alive HTTP server on port %d: %s", port, exc)

            logger.info("Starting Telegram Bot in Polling mode...")
            await self.app.updater.start_polling(drop_pending_updates=True)

    async def stop(self) -> None:
        """Gracefully stop the bot and keep-alive HTTP server."""
        if self.http_server:
            try:
                await self.http_server.stop()
            except Exception as exc:
                logger.warning("Error stopping keep-alive server: %s", exc)
            self.http_server = None

        if self.app:
            logger.info("Stopping Telegram Bot...")
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()
            logger.info("Telegram Bot shut down cleanly.")
