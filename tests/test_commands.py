"""Automated tests for Telegram bot commands and inline button callbacks."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.commands import (
    cmd_cancel,
    cmd_download,
    cmd_health,
    cmd_help,
    cmd_join,
    cmd_recordings,
    cmd_setname,
    cmd_start,
    cmd_status,
    cmd_stop,
    handle_callback_query,
    handle_message,
    set_job_manager,
)
from app.database import create_recording_job, create_recording_metadata, get_or_create_user
from app.job_manager import JobManager
from app.models import JobState


@pytest.fixture
def mock_job_manager():
    manager = MagicMock(spec=JobManager)
    manager.create_and_start_job = AsyncMock()
    manager.stop_job = AsyncMock(return_value=True)
    manager.cancel_job = AsyncMock(return_value=(True, "Cancelled successfully"))
    manager.get_active_worker_count = MagicMock(return_value=0)
    set_job_manager(manager)
    return manager


def _extract_reply_text(call_args):
    args, kwargs = call_args
    if "text" in kwargs:
        return kwargs["text"]
    return args[0] if args else ""


@pytest.mark.asyncio
async def test_cmd_start(mock_telegram_update):
    update, context = mock_telegram_update(user_id=1001)
    await cmd_start(update, context)
    update.effective_message.reply_text.assert_called_once()
    reply_text = _extract_reply_text(update.effective_message.reply_text.call_args)
    assert "Welcome to Zoom Cloud Recorder" in reply_text
    assert "Your Zoom Bot Name:" in reply_text


@pytest.mark.asyncio
async def test_cmd_setname(mock_telegram_update):
    """Verify one-time custom bot name input and retrieval."""
    # 1. No arguments: shows current name and instructions
    update, context = mock_telegram_update(user_id=1001, args=[])
    await cmd_setname(update, context)
    update.effective_message.reply_text.assert_called_once()
    text1 = _extract_reply_text(update.effective_message.reply_text.call_args)
    assert "Zoom Bot Participant Name Setup" in text1

    # 2. Set new custom name
    update2, context2 = mock_telegram_update(user_id=1001, args=["John's", "Custom", "Bot"])
    await cmd_setname(update2, context2)
    update2.effective_message.reply_text.assert_called_once()
    text2 = _extract_reply_text(update2.effective_message.reply_text.call_args)
    assert "Zoom Bot Name Saved Successfully" in text2
    assert "John's Custom Bot" in text2

    # 3. Check /start now displays the custom name
    update3, context3 = mock_telegram_update(user_id=1001)
    await cmd_start(update3, context3)
    text3 = _extract_reply_text(update3.effective_message.reply_text.call_args)
    assert "John's Custom Bot" in text3


@pytest.mark.asyncio
async def test_cmd_help(mock_telegram_update):
    update, context = mock_telegram_update(user_id=1001)
    await cmd_help(update, context)
    update.effective_message.reply_text.assert_called_once()
    args, _ = update.effective_message.reply_text.call_args
    assert "/join" in args[0]
    assert "/status" in args[0]
    assert "/stop" in args[0]


@pytest.mark.asyncio
async def test_cmd_join_success(mock_telegram_update, mock_job_manager):
    update, context = mock_telegram_update(
        user_id=1001,
        args=["https://us02web.zoom.us/j/1234567890?pwd=secret"],
    )

    fake_job = MagicMock()
    fake_job.job_id = "REC-1001"
    fake_job.meeting_id = "1234567890"
    mock_job_manager.create_and_start_job.return_value = fake_job

    await cmd_join(update, context)
    update.effective_message.reply_text.assert_called_once()
    args, _ = update.effective_message.reply_text.call_args
    assert "Recording request received" in args[0]
    assert "REC-1001" in args[0]


@pytest.mark.asyncio
async def test_cmd_join_missing_url(mock_telegram_update, mock_job_manager):
    update, context = mock_telegram_update(user_id=1001, args=[])
    await cmd_join(update, context)
    update.effective_message.reply_text.assert_called_once()
    args, _ = update.effective_message.reply_text.call_args
    assert "Missing Zoom Meeting Link" in args[0]


@pytest.mark.asyncio
async def test_cmd_status(mock_telegram_update, mock_job_manager):
    update, context = mock_telegram_update(user_id=1001)
    # When no jobs exist
    await cmd_status(update, context)
    update.effective_message.reply_text.assert_called_once()
    args, _ = update.effective_message.reply_text.call_args
    assert "No Active Recording" in args[0]


@pytest.mark.asyncio
async def test_cmd_stop(mock_telegram_update, mock_job_manager):
    update, context = mock_telegram_update(user_id=1001)
    user = await get_or_create_user(telegram_id=1001)

    # Create active recording
    await create_recording_job(
        user_id=user.id,
        job_id="REC-STOP-TEST",
        zoom_url="https://zoom.us/j/123",
        meeting_id="123",
        passcode=None,
        display_name="Bot",
    )

    await cmd_stop(update, context)
    update.effective_message.reply_text.assert_called_once()
    args, _ = update.effective_message.reply_text.call_args
    assert "Stop Signal Sent" in args[0]


@pytest.mark.asyncio
async def test_cmd_cancel(mock_telegram_update, mock_job_manager):
    update, context = mock_telegram_update(user_id=1001)
    user = await get_or_create_user(telegram_id=1001)

    await create_recording_job(
        user_id=user.id,
        job_id="REC-CANCEL-TEST",
        zoom_url="https://zoom.us/j/123",
        meeting_id="123",
        passcode=None,
        display_name="Bot",
    )

    await cmd_cancel(update, context)
    update.effective_message.reply_text.assert_called_once()
    args, _ = update.effective_message.reply_text.call_args
    assert "Job Cancelled" in args[0]


@pytest.mark.asyncio
async def test_cmd_recordings_and_download(mock_telegram_update):
    user = await get_or_create_user(telegram_id=1001)
    await create_recording_metadata(
        recording_id="REC-MED-DOWNLOAD",
        job_id="REC-JOB-99",
        user_id=user.id,
        file_path="/tmp/test.mp4",
        storage_key="recordings/test.mp4",
        storage_provider="local",
        duration_seconds=60.0,
        file_size_bytes=5242880,
    )

    update, context = mock_telegram_update(user_id=1001)
    await cmd_recordings(update, context)
    update.effective_message.reply_text.assert_called_once()
    recs_text = _extract_reply_text(update.effective_message.reply_text.call_args)
    assert "REC-MED-DOWNLOAD" in recs_text

    # Test download command
    update_dl, context_dl = mock_telegram_update(user_id=1001, args=["REC-MED-DOWNLOAD"])
    await cmd_download(update_dl, context_dl)
    update_dl.effective_message.reply_text.assert_called_once()
    dl_text = _extract_reply_text(update_dl.effective_message.reply_text.call_args)
    assert "Local Recording File" in dl_text or "Download Ready" in dl_text


@pytest.mark.asyncio
async def test_cmd_health(mock_telegram_update, mock_job_manager):
    update, context = mock_telegram_update(user_id=1001)
    await cmd_health(update, context)
    update.effective_message.reply_text.assert_called_once()
    args, _ = update.effective_message.reply_text.call_args
    assert "System Health Report" in args[0]
    assert "ONLINE" in args[0]


@pytest.mark.asyncio
async def test_handle_message_plain_text(mock_telegram_update):
    """Verify plain text greeting returns dashboard and command guide."""
    update, context = mock_telegram_update(user_id=1001, text="hello bot")
    await handle_message(update, context)
    update.effective_message.reply_text.assert_called_once()
    reply = _extract_reply_text(update.effective_message.reply_text.call_args)
    assert "Hi Test!" in reply
    assert "paste a Zoom meeting link" in reply


@pytest.mark.asyncio
async def test_handle_message_raw_zoom_link(mock_telegram_update, mock_job_manager):
    """Verify pasting a raw Zoom link directly triggers meeting join."""
    raw_link = "https://us02web.zoom.us/j/1234567890?pwd=secretpassword"
    update, context = mock_telegram_update(user_id=1001, text=raw_link)
    
    mock_job = MagicMock()
    mock_job.job_id = "JOB-TEST-AUTO"
    mock_job.display_name = "Cloud Recorder Bot"
    mock_job_manager.create_and_start_job.return_value = mock_job

    await handle_message(update, context)
    update.effective_message.reply_text.assert_called_once()
    reply = _extract_reply_text(update.effective_message.reply_text.call_args)
    assert "Recording request received" in reply
    assert "JOB-TEST-AUTO" in reply

