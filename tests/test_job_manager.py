"""Automated tests for JobManager concurrency, job creation, and state rules."""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from app.database import create_recording_job, get_or_create_user, get_recording_job, update_job_status
from app.job_manager import JobManager
from app.models import JobState


@pytest.mark.asyncio
async def test_job_id_generation():
    """Verify sequential REC-1001, REC-1002 ID generation."""
    manager = JobManager()
    id1 = await manager.generate_job_id()
    assert id1 == "REC-1001"

    user = await get_or_create_user(telegram_id=1001)
    await create_recording_job(
        user_id=user.id,
        job_id=id1,
        zoom_url="https://zoom.us/j/123",
        meeting_id="123",
        passcode=None,
        display_name="Bot",
    )

    id2 = await manager.generate_job_id()
    assert id2 == "REC-1002"


@pytest.mark.asyncio
async def test_prevent_duplicate_active_recording():
    """Verify a user cannot launch multiple simultaneous recordings."""
    manager = JobManager()
    user = await get_or_create_user(telegram_id=1001)

    # Mock background worker run
    with patch("app.job_manager.RecordingWorker") as mock_worker_cls:
        mock_worker = mock_worker_cls.return_value
        mock_worker.run = AsyncMock()

        job1 = await manager.create_and_start_job(
            user=user,
            telegram_chat_id=999,
            zoom_url="https://app.zoom.us/wc/join/111222333",
            meeting_id="111222333",
        )
        assert job1.job_id == "REC-1001"

        # Attempting second job for same user while job1 is active should fail
        with pytest.raises(ValueError, match="already have an active recording"):
            await manager.create_and_start_job(
                user=user,
                telegram_chat_id=999,
                zoom_url="https://app.zoom.us/wc/join/444555666",
                meeting_id="444555666",
            )


@pytest.mark.asyncio
async def test_max_concurrency_limit():
    """Verify global concurrency limit is enforced."""
    manager = JobManager()
    manager.settings.max_concurrent_jobs = 1

    user1 = await get_or_create_user(telegram_id=1001)
    user2 = await get_or_create_user(telegram_id=1002)

    worker_running = asyncio.Event()

    async def fake_run():
        await worker_running.wait()

    with patch("app.job_manager.RecordingWorker") as mock_worker_cls:
        mock_worker = mock_worker_cls.return_value
        mock_worker.run = fake_run

        await manager.create_and_start_job(
            user=user1,
            telegram_chat_id=1001,
            zoom_url="https://zoom.us/j/111",
            meeting_id="111",
        )

        # Attempting job with user2 when limit is 1 should raise capacity error
        with pytest.raises(ValueError, match="Worker capacity reached"):
            await manager.create_and_start_job(
                user=user2,
                telegram_chat_id=1002,
                zoom_url="https://zoom.us/j/222",
                meeting_id="222",
            )

        # Release worker
        worker_running.set()


@pytest.mark.asyncio
async def test_cancel_vs_stop_rules():
    """Verify that RECORDING jobs cannot be cancelled (must be stopped)."""
    manager = JobManager()
    user = await get_or_create_user(telegram_id=1001)

    job = await create_recording_job(
        user_id=user.id,
        job_id="REC-TEST-CANCEL",
        zoom_url="https://zoom.us/j/555",
        meeting_id="555",
        passcode=None,
        display_name="Bot",
    )

    # In QUEUED state -> can be cancelled
    success, msg = await manager.cancel_job("REC-TEST-CANCEL", user.id)
    assert success is True
    assert "cancelled" in msg.lower()

    # Re-create and transition to RECORDING
    job2 = await create_recording_job(
        user_id=user.id,
        job_id="REC-TEST-RECORDING",
        zoom_url="https://zoom.us/j/666",
        meeting_id="666",
        passcode=None,
        display_name="Bot",
    )
    await update_job_status("REC-TEST-RECORDING", JobState.RECORDING)

    # In RECORDING state -> CANNOT be cancelled! Must use stop!
    success2, msg2 = await manager.cancel_job("REC-TEST-RECORDING", user.id)
    assert success2 is False
    assert "cannot be cancelled in state 'RECORDING'" in msg2
