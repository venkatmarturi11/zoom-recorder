"""Telegram bot command handlers and interactive callback query logic."""

import logging
import re
from typing import Optional
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from app.auth import authorized_telegram_user_only
from app.config import get_settings
from app.database import (
    get_active_job_for_user,
    get_or_create_user,
    get_recording,
    get_recording_job,
    get_recordings_for_user,
    get_system_health_stats,
    set_user_display_name,
)
from app.job_manager import JobManager
from app.models import JobState
from app.security import InvalidZoomURLError, SSRFAttemptError, validate_and_parse_zoom_url
from app.storage import get_storage_provider

logger = logging.getLogger("zoom_recorder.commands")

# Global reference initialized during application startup
job_manager: Optional[JobManager] = None


def set_job_manager(manager: JobManager) -> None:
    global job_manager
    job_manager = manager


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Return the persistent bottom menu bar keyboard docked below chat input."""
    keyboard = [
        [KeyboardButton("🎥 Join Meeting"), KeyboardButton("📊 Status")],
        [KeyboardButton("⏹️ Stop Recording"), KeyboardButton("📁 Recordings")],
        [KeyboardButton("🏷️ Set Bot Name"), KeyboardButton("🛟 Help")],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


@authorized_telegram_user_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with mobile-friendly dashboard and one-time name status."""
    user = update.effective_user
    db_user = await get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    configured_name = db_user.custom_display_name
    settings = get_settings()
    active_bot_name = configured_name or settings.bot_display_name

    text = (
        f"👋 *Welcome to Zoom Cloud Recorder, {user.first_name}!*\n\n"
        "This service records Zoom meetings directly in a secure cloud browser and "
        "delivers MP4 recordings with full video and audio directly to your phone.\n\n"
        f"🏷️ *Your Zoom Bot Name:* `{active_bot_name}`\n"
    )

    if not configured_name:
        text += (
            "⚠️ _You haven't set a custom name yet. The bot will join with the default name._\n"
            "👉 *One-time setup:* Tap *'Set Bot Name'* below or send `/setname Your Name`.\n\n"
        )
    else:
        text += "✅ _Configured and ready for all future recordings._\n\n"

    text += (
        "📋 *Requirements for Recording:*\n"
        "• Valid Zoom meeting link (with passcode if required)\n"
        "• Meeting host must admit the recording participant\n\n"
        "🚀 *Quick Controls:*"
    )

    keyboard = [
        [
            InlineKeyboardButton("📊 Current Status", callback_data="cmd_status"),
            InlineKeyboardButton("📁 My Recordings", callback_data="cmd_recordings"),
        ],
        [
            InlineKeyboardButton("🏷️ Set Bot Name", callback_data="cmd_setname"),
            InlineKeyboardButton("🩺 System Health", callback_data="cmd_health"),
        ],
        [
            InlineKeyboardButton("ℹ️ Help & Instructions", callback_data="cmd_help"),
        ],
    ]

    await update.effective_message.reply_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard(),
    )


@authorized_telegram_user_only
async def cmd_setname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /setname <name> command allowing one-time input for the Zoom bot participant name."""
    tg_user = update.effective_user
    db_user = await get_or_create_user(telegram_id=tg_user.id)
    settings = get_settings()

    if not context.args or len(context.args) == 0:
        current = db_user.custom_display_name or settings.bot_display_name
        is_custom = bool(db_user.custom_display_name)
        status_label = "Your Custom Name" if is_custom else "Default System Name"

        await update.effective_message.reply_text(
            f"🏷️ *Zoom Bot Participant Name Setup*\n\n"
            f"• *Current Name:* `{current}`\n"
            f"• *Type:* _{status_label}_\n\n"
            "To set or update your one-time name, reply with:\n"
            "`/setname Your Name`\n\n"
            "Examples:\n"
            "• `/setname John Doe (Notes)`\n"
            "• `/setname Alex Recording Bot`\n"
            "• `/setname Acme Corp Assistant`\n\n"
            "_This name is saved and will be reused automatically for all your future recordings._",
            parse_mode="Markdown",
        )
        return

    new_name = " ".join(context.args).strip()
    if len(new_name) > 64:
        new_name = new_name[:64]

    await set_user_display_name(telegram_id=tg_user.id, display_name=new_name)

    await update.effective_message.reply_text(
        f"✅ *Zoom Bot Name Saved Successfully!*\n\n"
        f"From now on, your cloud recorder will join meetings as:\n"
        f"👉 `{new_name}`\n\n"
        "You only need to set this once! Send `/join <zoom_url>` whenever you're ready to record.",
        parse_mode="Markdown",
    )


@authorized_telegram_user_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command with full documentation."""
    help_text = (
        "📖 *Zoom Recorder Bot — Command Reference*\n\n"
        "• `/setname <name>` — Set your one-time custom Zoom bot participant name\n"
        "• `/join <zoom_link>` — Start cloud browser recording of a Zoom meeting\n"
        "• `/status [job_id]` — Check state of current or specified recording\n"
        "• `/stop [job_id]` — Gracefully stop recording and upload MP4 file\n"
        "• `/cancel [job_id]` — Cancel a queued or pending job\n"
        "• `/recordings` — View your previous meeting recordings\n"
        "• `/download <recording_id>` — Get download link for a recording\n"
        "• `/health` — Check controller, worker, and storage health\n"
        "• `/help` — Display this guide\n\n"
        "🛡️ *Ethical & Authorized Recording Policy:*\n"
        "This bot does not bypass host permissions, suppress notifications, or secretly "
        "record. It joins transparently using your configured participant name."
    )
    await update.effective_message.reply_text(help_text, parse_mode="Markdown")


@authorized_telegram_user_only
async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /join <zoom_link> [optional_custom_name] command."""
    if not job_manager:
        await update.effective_message.reply_text("⚠️ System initializing. Please retry in a moment.")
        return

    # Check argument
    if not context.args or len(context.args) == 0:
        await update.effective_message.reply_text(
            "⚠️ *Missing Zoom Meeting Link*\n\n"
            "Usage: `/join <zoom_url>`\n"
            "Example: `/join https://us02web.zoom.us/j/1234567890?pwd=secret`\n\n"
            "_Note: You can configure your permanent Zoom participant name with `/setname <Your Name>`._",
            parse_mode="Markdown",
        )
        return

    raw_url = context.args[0]
    display_name_override = " ".join(context.args[1:]).strip() if len(context.args) > 1 else None
    tg_user = update.effective_user

    # 1. Validate Zoom URL & SSRF
    try:
        normalized_url, meeting_id, passcode = validate_and_parse_zoom_url(raw_url)
    except InvalidZoomURLError as err:
        await update.effective_message.reply_text(
            f"❌ *Invalid Zoom URL*\n\n{str(err)}",
            parse_mode="Markdown",
        )
        return
    except SSRFAttemptError as err:
        logger.warning("SSRF blocked for user %d: %s", tg_user.id, err)
        await update.effective_message.reply_text(
            "⛔ *Security Error*\nThe provided URL cannot be accessed.",
            parse_mode="Markdown",
        )
        return

    db_user = await get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
    )

    # 2. Create and start job via JobManager
    try:
        job = await job_manager.create_and_start_job(
            user=db_user,
            telegram_chat_id=update.effective_chat.id,
            zoom_url=normalized_url,
            meeting_id=meeting_id,
            passcode=passcode,
            display_name_override=display_name_override,
        )

        response_text = (
            "Recording request received.\n\n"
            f"*Job ID:* `{job.job_id}`\n"
            f"*Participant Name:* `{job.display_name}`\n"
            f"*Status:* `Preparing browser`\n"
            f"*Meeting:* `Validating access (ID: {meeting_id})`\n\n"
            "_You will receive another notification when the recording starts._"
        )

        keyboard = [
            [
                InlineKeyboardButton("📊 Check Status", callback_data=f"status_{job.job_id}"),
                InlineKeyboardButton("⏹️ Stop Recording", callback_data=f"stop_{job.job_id}"),
            ],
            [
                InlineKeyboardButton("🚫 Cancel Job", callback_data=f"cancel_{job.job_id}"),
            ],
        ]

        await update.effective_message.reply_text(
            response_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except ValueError as val_err:
        await update.effective_message.reply_text(f"⚠️ *Notice:* {str(val_err)}", parse_mode="Markdown")
    except Exception as exc:
        logger.error("Failed to start recording job: %s", exc, exc_info=True)
        await update.effective_message.reply_text(
            "❌ *Error Launching Recording*\nAn internal error occurred while scheduling the recording worker.",
            parse_mode="Markdown",
        )


@authorized_telegram_user_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status [job_id] command."""
    tg_user = update.effective_user
    db_user = await get_or_create_user(telegram_id=tg_user.id)

    target_job = None
    if context.args and len(context.args) > 0:
        target_job = await get_recording_job(context.args[0].strip().upper())
        if not target_job or target_job.user_id != db_user.id:
            await update.effective_message.reply_text("❌ Recording job not found.")
            return
    else:
        # Fetch current active job for user
        target_job = await get_active_job_for_user(db_user.id)

    if not target_job:
        await update.effective_message.reply_text(
            "ℹ️ *No Active Recording*\n\n"
            "Status: `IDLE`\n\n"
            "Use `/join <zoom_url>` to initiate a recording.",
            parse_mode="Markdown",
        )
        return

    text = (
        f"📊 *Job Status: {target_job.job_id}*\n\n"
        f"• *Status:* `{target_job.status.value}`\n"
        f"• *Meeting ID:* `{target_job.meeting_id}`\n"
        f"• *Created:* `{target_job.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}`\n"
    )
    if target_job.status_detail:
        text += f"• *Detail:* _{target_job.status_detail}_\n"
    if target_job.error_message:
        text += f"• *Error:* `{target_job.error_message}`\n"

    buttons = []
    if target_job.status in (JobState.RECORDING, JobState.JOINING_MEETING, JobState.WAITING_FOR_HOST):
        buttons.append([
            InlineKeyboardButton("⏹️ Stop Recording", callback_data=f"stop_{target_job.job_id}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"status_{target_job.job_id}"),
        ])
    elif target_job.status == JobState.QUEUED:
        buttons.append([
            InlineKeyboardButton("🚫 Cancel Job", callback_data=f"cancel_{target_job.job_id}"),
        ])

    keyboard = InlineKeyboardMarkup(buttons) if buttons else None
    await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


@authorized_telegram_user_only
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop [job_id] command."""
    if not job_manager:
        await update.effective_message.reply_text("⚠️ System initializing.")
        return

    tg_user = update.effective_user
    db_user = await get_or_create_user(telegram_id=tg_user.id)

    target_job = None
    if context.args and len(context.args) > 0:
        target_job = await get_recording_job(context.args[0].strip().upper())
    else:
        target_job = await get_active_job_for_user(db_user.id)

    if not target_job or target_job.user_id != db_user.id:
        await update.effective_message.reply_text("❌ No active recording found to stop.")
        return

    res = await job_manager.stop_job(target_job.job_id, db_user.id)
    if isinstance(res, tuple):
        success, msg = res
    else:
        success = bool(res)
        msg = "Packaging and uploading MP4... You will receive the download link shortly." if success else "Job is not actively recording or already stopped."

    if success:
        await update.effective_message.reply_text(
            f"⏹️ *Stop Signal Sent*\n\n"
            f"Finalizing session for `{target_job.job_id}`.\n"
            f"{msg}",
            parse_mode="Markdown",
        )
    else:
        await update.effective_message.reply_text(f"⚠️ {msg}")


@authorized_telegram_user_only
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel [job_id] command."""
    if not job_manager:
        await update.effective_message.reply_text("⚠️ System initializing.")
        return

    tg_user = update.effective_user
    db_user = await get_or_create_user(telegram_id=tg_user.id)

    target_job = None
    if context.args and len(context.args) > 0:
        target_job = await get_recording_job(context.args[0].strip().upper())
    else:
        target_job = await get_active_job_for_user(db_user.id)

    if not target_job or target_job.user_id != db_user.id:
        await update.effective_message.reply_text("❌ No eligible job found to cancel.")
        return

    success, msg = await job_manager.cancel_job(target_job.job_id, db_user.id)
    if success:
        await update.effective_message.reply_text(f"🚫 *Job Cancelled*\n\n{msg}", parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(f"⚠️ *Cannot Cancel:*\n{msg}", parse_mode="Markdown")


@authorized_telegram_user_only
async def cmd_recordings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /recordings command listing user's recording history."""
    tg_user = update.effective_user
    db_user = await get_or_create_user(telegram_id=tg_user.id)
    recs = await get_recordings_for_user(db_user.id, limit=10)

    if not recs:
        await update.effective_message.reply_text(
            "📁 *Your Recordings*\n\n"
            "You have no completed recordings yet.\n"
            "Use `/join <zoom_url>` to record your first meeting!",
            parse_mode="Markdown",
        )
        return

    text = "📁 *Your Recent Recordings:*\n\n"
    keyboard = []

    for idx, r in enumerate(recs, 1):
        date_str = r.created_at.strftime("%b %d, %Y %H:%M")
        dur_str = f"{int(r.duration_seconds // 60)}m" if r.duration_seconds else "N/A"
        size_str = f"{r.file_size_bytes / (1024 * 1024):.1f} MB" if r.file_size_bytes else "N/A"

        text += (
            f"*{idx}. {r.recording_id}* ({r.video_format.upper()})\n"
            f"📅 {date_str} | ⏱️ {dur_str} | 💾 {size_str}\n"
            f"Status: `{r.upload_status}`\n\n"
        )
        keyboard.append([
            InlineKeyboardButton(f"⬇️ Download {r.recording_id}", callback_data=f"download_{r.recording_id}")
        ])

    await update.effective_message.reply_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


@authorized_telegram_user_only
async def cmd_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /download <recording_id> command."""
    if not context.args or len(context.args) == 0:
        await update.effective_message.reply_text(
            "⚠️ Usage: `/download <recording_id>`\nExample: `/download REC-MED-A1B2C3D4`",
            parse_mode="Markdown",
        )
        return

    rec_id = context.args[0].strip().upper()
    tg_user = update.effective_user
    db_user = await get_or_create_user(telegram_id=tg_user.id)
    rec = await get_recording(rec_id)

    if not rec or rec.user_id != db_user.id:
        await update.effective_message.reply_text("❌ Recording not found or access denied.")
        return

    storage = get_storage_provider()
    download_url = await storage.get_download_url(rec.storage_key)

    if download_url.startswith("http://") or download_url.startswith("https://"):
        keyboard = [[InlineKeyboardButton("⬇️ Direct Download Link", url=download_url)]]
        await update.effective_message.reply_text(
            f"🎬 *Download Ready for {rec.recording_id}*\n\n"
            "Tap below to stream or download your MP4 recording securely on your phone:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        # Local development storage
        await update.effective_message.reply_text(
            f"🎬 *Local Recording File:*\n`{download_url}`",
            parse_mode="Markdown",
        )


@authorized_telegram_user_only
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /health command."""
    settings = get_settings()
    stats = await get_system_health_stats()
    active_workers = job_manager.get_active_worker_count() if job_manager else 0

    text = (
        "🩺 *System Health Report*\n\n"
        "• *Controller Status:* `ONLINE` ✅\n"
        f"• *Active Workers:* `{active_workers} / {settings.max_concurrent_jobs}`\n"
        f"• *Storage Provider:* `{settings.storage_provider.upper()}`\n"
        f"• *Active Jobs:* `{stats['active_jobs']}`\n"
        f"• *Total Jobs Recorded:* `{stats['total_jobs']}`\n"
        f"• *Total Recordings:* `{stats['total_recordings']}`\n"
        f"• *Capture Driver:* `{settings.capture_driver}`\n"
        f"• *Resolution:* `{settings.recording_resolution}` @ {settings.recording_fps}fps\n"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks (stop, status, cancel, download)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    tg_user = update.effective_user
    db_user = await get_or_create_user(telegram_id=tg_user.id)

    if data.startswith("stop_"):
        job_id = data.replace("stop_", "")
        if job_manager:
            res = await job_manager.stop_job(job_id, db_user.id)
            if isinstance(res, tuple):
                success, msg = res
            else:
                success = bool(res)
                msg = "Finalizing media." if success else "Could not stop recording."

            if success:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(f"⏹️ Stopping `{job_id}`: {msg}")
            else:
                await query.message.reply_text(f"ℹ️ {msg}")

    elif data.startswith("status_"):
        job_id = data.replace("status_", "")
        job = await get_recording_job(job_id)
        if job:
            await query.message.reply_text(f"📊 *{job.job_id} Status:* `{job.status.value}`", parse_mode="Markdown")

    elif data.startswith("cancel_"):
        job_id = data.replace("cancel_", "")
        if job_manager:
            success, msg = await job_manager.cancel_job(job_id, db_user.id)
            await query.message.reply_text(f"🚫 {msg}")

    elif data.startswith("download_"):
        rec_id = data.replace("download_", "")
        rec = await get_recording(rec_id)
        if rec:
            storage = get_storage_provider()
            url = await storage.get_download_url(rec.storage_key)
            if url.startswith("http"):
                keyboard = [[InlineKeyboardButton("⬇️ Open Download Link", url=url)]]
                await query.message.reply_text(
                    f"🎬 Recording `{rec.recording_id}` link:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await query.message.reply_text(f"📁 Local file: `{url}`")

    elif data == "cmd_status":
        await cmd_status(update, context)
    elif data == "cmd_recordings":
        await cmd_recordings(update, context)
    elif data == "cmd_help":
        await cmd_help(update, context)
    elif data == "cmd_health":
        await cmd_health(update, context)
    elif data == "cmd_setname":
        await cmd_setname(update, context)


@authorized_telegram_user_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages from Telegram users.

    - Automatically detects pasted Zoom URLs and initiates recording (/join).
    - If plain text or greeting is sent, replies with an interactive dashboard/guide.
    """
    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip()

    # Handle persistent bottom menu bar button taps
    if text in ("🎥 Join Meeting", "Join Meeting"):
        await update.effective_message.reply_text(
            "🎥 *Join & Record Zoom Meeting*\n\n"
            "Please send or paste your Zoom meeting link directly here in the chat:\n"
            "Example: `https://zoom.us/j/1234567890?pwd=secret`\n\n"
            "_The bot will immediately join and record the meeting!_",
            parse_mode="Markdown",
            reply_markup=get_main_menu_keyboard(),
        )
        return
    elif text in ("📊 Status", "Status"):
        await cmd_status(update, context)
        return
    elif text in ("⏹️ Stop Recording", "Stop Recording", "Stop"):
        await cmd_stop(update, context)
        return
    elif text in ("📁 Recordings", "Recordings", "📁 My Recordings"):
        await cmd_recordings(update, context)
        return
    elif text in ("🏷️ Set Bot Name", "Set Bot Name"):
        await cmd_setname(update, context)
        return
    elif text in ("🛟 Help", "Help", "ℹ️ Help"):
        await cmd_help(update, context)
        return

    # Search for Zoom URL in message
    zoom_match = re.search(r"https?://[^\s]*zoom(?:gov)?\.(?:us|com)/[^\s]+", text, re.IGNORECASE)
    if not zoom_match:
        # Check without https://
        zoom_match = re.search(r"(?:[a-zA-Z0-9-]+\.)?zoom(?:gov)?\.(?:us|com)/[^\s]+", text, re.IGNORECASE)

    if zoom_match:
        raw_url = zoom_match.group(0).rstrip(".,;!>")
        # Pass to cmd_join via context.args
        context.args = [raw_url]
        await cmd_join(update, context)
        return

    # Regular conversational message / greeting
    user = update.effective_user
    db_user = await get_or_create_user(telegram_id=user.id)
    settings = get_settings()
    active_bot_name = db_user.custom_display_name or settings.bot_display_name

    reply = (
        f"👋 *Hi {user.first_name}!*\n\n"
        "To start recording, simply *send or paste a Zoom meeting link* here or tap *'🎥 Join Meeting'* below!\n\n"
        f"🏷️ *Bot Participant Name:* `{active_bot_name}`\n\n"
        "🚀 *Commands & Quick Controls:*\n"
        "• 🎥 *Join Meeting* - Paste any Zoom link\n"
        "• 📊 *Status* - Check current recording status\n"
        "• ⏹️ *Stop Recording* - Stop active recording & get MP4\n"
        "• 📁 *Recordings* - View past recordings & download links\n"
        "• 🏷️ *Set Bot Name* - Change your bot's name in meetings\n"
        "• 🛟 *Help* - View complete instructions"
    )

    await update.effective_message.reply_text(
        text=reply,
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard(),
    )

