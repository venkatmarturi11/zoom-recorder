"""Automated tests for database persistence, repositories, and state queries."""

import pytest
from app.database import (
    create_recording_job,
    create_recording_metadata,
    get_active_job_for_user,
    get_or_create_user,
    get_recording,
    get_recording_job,
    get_recordings_for_user,
    get_system_health_stats,
    mark_orphaned_jobs_as_failed,
    update_job_status,
)
from app.models import JobState


@pytest.mark.asyncio
async def test_user_creation_and_update():
    """Verify user persistence and permission initialization."""
    user = await get_or_create_user(
        telegram_id=1001,
        username="john_doe",
        first_name="John",
        last_name="Doe",
    )
    assert user.id is not None
    assert user.telegram_id == 1001
    assert user.username == "john_doe"
    assert user.is_allowed is True
    assert user.is_admin is True

    # Re-fetch should return existing user and update changes
    updated = await get_or_create_user(
        telegram_id=1001,
        username="john_updated",
    )
    assert updated.id == user.id
    assert updated.username == "john_updated"


@pytest.mark.asyncio
async def test_recording_job_lifecycle():
    """Verify job creation, state transitions, and active job queries."""
    user = await get_or_create_user(telegram_id=1002, username="alice")

    job = await create_recording_job(
        user_id=user.id,
        job_id="REC-TEST-1",
        zoom_url="https://app.zoom.us/wc/join/1234567890",
        meeting_id="1234567890",
        passcode="123456",
        display_name="Zoom Bot",
    )
    assert job.status == JobState.QUEUED

    # Should be detected as active job
    active = await get_active_job_for_user(user.id)
    assert active is not None
    assert active.job_id == "REC-TEST-1"

    # Transition to RECORDING
    await update_job_status("REC-TEST-1", JobState.RECORDING, "Capturing tab")
    fetched = await get_recording_job("REC-TEST-1")
    assert fetched.status == JobState.RECORDING
    assert fetched.started_at is not None

    # Transition to COMPLETED
    await update_job_status("REC-TEST-1", JobState.COMPLETED, "Done")
    completed = await get_recording_job("REC-TEST-1")
    assert completed.status == JobState.COMPLETED
    assert completed.completed_at is not None

    # Now active job query should return None
    assert await get_active_job_for_user(user.id) is None


@pytest.mark.asyncio
async def test_orphaned_jobs_recovery():
    """Verify incomplete jobs are safely marked FAILED on worker startup."""
    user = await get_or_create_user(telegram_id=1001)

    # Simulate crashed job stuck in RECORDING
    job = await create_recording_job(
        user_id=user.id,
        job_id="REC-CRASHED",
        zoom_url="https://zoom.us/j/999",
        meeting_id="999",
        passcode=None,
        display_name="Bot",
    )
    await update_job_status("REC-CRASHED", JobState.RECORDING)

    # Run cleanup
    recovered = await mark_orphaned_jobs_as_failed()
    assert recovered == 1

    updated = await get_recording_job("REC-CRASHED")
    assert updated.status == JobState.FAILED
    assert "restarted" in updated.error_message


@pytest.mark.asyncio
async def test_recording_metadata_and_stats():
    """Verify recording metadata persistence and system health stats."""
    user = await get_or_create_user(telegram_id=1001)
    job = await create_recording_job(
        user_id=user.id,
        job_id="REC-META",
        zoom_url="https://zoom.us/j/111",
        meeting_id="111",
        passcode=None,
        display_name="Bot",
    )

    rec = await create_recording_metadata(
        recording_id="REC-MED-1234",
        job_id=job.job_id,
        user_id=user.id,
        file_path="/tmp/rec.mp4",
        storage_key="recordings/rec.mp4",
        storage_provider="local",
        duration_seconds=125.5,
        file_size_bytes=10485760,
        resolution="1280x720",
    )
    assert rec.recording_id == "REC-MED-1234"

    fetched = await get_recording("REC-MED-1234")
    assert fetched is not None
    assert fetched.duration_seconds == 125.5

    user_recs = await get_recordings_for_user(user.id)
    assert len(user_recs) == 1
    assert user_recs[0].recording_id == "REC-MED-1234"

    stats = await get_system_health_stats()
    assert stats["total_jobs"] >= 1
    assert stats["total_recordings"] >= 1
