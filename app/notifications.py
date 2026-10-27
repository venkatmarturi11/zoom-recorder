"""Telegram notification service delivering real-time lifecycle updates to users."""

import logging
from typing import Optional
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import get_settings
from app.models import JobState, RecordingJob

logger = logging.getLogger("zoom_recorder.notifications")


class NotificationService:
    """Delivers asynchronous status updates and completed download links to Telegram users."""

    def __init__(self, bot: Optional[Bot] = None):
        self.bot = bot
        self.settings = get_settings()

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    async def notify_job_status(
        self,
        chat_id: int,
        job: RecordingJob,
        status: JobState,
        detail: Optional[str] = None,
    ) -> None:
        """Send formatted state update to the user's Telegram chat."""
        if not self.bot:
            logger.warning("Bot instance not set; skipping notification to chat %d", chat_id)
            return

        status_emojis = {
            JobState.VALIDATING: "🔍",
            JobState.QUEUED: "⏳",
            JobState.STARTING_BROWSER: "🌐",
            JobState.JOINING_MEETING: "🚪",
            JobState.WAITING_FOR_HOST: "⏱️",
            JobState.WAITING_FOR_AUTHORIZATION: "🔒",
            JobState.PREPARING_CAPTURE: "🎥",
            JobState.RECORDING: "🔴",
            JobState.STOPPING: "⏹️",
            JobState.UPLOADING: "☁️",
            JobState.COMPLETED: "✅",
            JobState.FAILED: "❌",
            JobState.CANCELLED: "🚫",
        }

        emoji = status_emojis.get(status, "ℹ️")
        text = (
            f"{emoji} *Recording Update*\n\n"
            f"*Job ID:* `{job.job_id}`\n"
            f"*Meeting ID:* `{job.meeting_id}`\n"
            f"*Status:* `{status.value}`\n"
        )
        if detail:
            text += f"*Detail:* _{detail}_\n"

        # Action buttons according to state
        buttons = []
        if status in (JobState.RECORDING, JobState.JOINING_MEETING, JobState.WAITING_FOR_HOST):
            buttons.append([
                InlineKeyboardButton("⏹️ Stop Recording", callback_data=f"stop_{job.job_id}"),
                InlineKeyboardButton("🔄 Refresh Status", callback_data=f"status_{job.job_id}"),
            ])
        elif status == JobState.QUEUED:
            buttons.append([
                InlineKeyboardButton("🚫 Cancel Job", callback_data=f"cancel_{job.job_id}"),
            ])

        keyboard = InlineKeyboardMarkup(buttons) if buttons else None

        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.warning("Markdown send failed for chat %d: %s; retrying as plain text", chat_id, exc)
            try:
                plain_text = text.replace("*", "").replace("`", "").replace("_", "")
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=plain_text,
                    reply_markup=keyboard,
                )
            except Exception as final_exc:
                logger.error("Failed to send notification to chat %d: %s", chat_id, final_exc)

    async def notify_recording_completed(
        self,
        chat_id: int,
        job_id: str,
        recording_id: str,
        duration_seconds: Optional[float],
        file_size_bytes: Optional[int],
        download_url: str,
    ) -> None:
        """Send completion message with direct authenticated download button."""
        if not self.bot:
            return

        duration_str = "N/A"
        if duration_seconds:
            mins = int(duration_seconds // 60)
            secs = int(duration_seconds % 60)
            duration_str = f"{mins}m {secs}s"

        size_str = "N/A"
        if file_size_bytes:
            mb = file_size_bytes / (1024 * 1024)
            size_str = f"{mb:.2f} MB"

        text = (
            "🎉 *Recording Completed Successfully!*\n\n"
            f"*Recording ID:* `{recording_id}`\n"
            f"*Job ID:* `{job_id}`\n"
            f"*Duration:* {duration_str}\n"
            f"*File Size:* {size_str}\n"
            f"*Format:* MP4 (H.264 / AAC)\n\n"
            "Tap below to download or view your recording:"
        )

        buttons = []
        if download_url.startswith("http://") or download_url.startswith("https://"):
            buttons.append([InlineKeyboardButton("⬇️ Download Recording", url=download_url)])
        else:
            # Fallback for local storage development mode
            buttons.append([InlineKeyboardButton("ℹ️ View Info", callback_data=f"download_{recording_id}")])

        keyboard = InlineKeyboardMarkup(buttons)

        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.error("Failed to send completion notification to %d: %s", chat_id, exc)

    async def send_custom_message(
        self,
        chat_id: int,
        message_text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        """Send custom text notification to a specific chat."""
        if not self.bot:
            logger.warning("Bot instance not set; skipping message to chat %d", chat_id)
            return
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
        except Exception as exc:
            try:
                plain_text = message_text.replace("*", "").replace("`", "").replace("_", "")
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=plain_text,
                    reply_markup=reply_markup,
                )
            except Exception as final_exc:
                logger.error("Failed to send message to %d: %s", chat_id, final_exc)

