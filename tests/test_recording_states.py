"""Automated tests covering all 14 JobState lifecycle states and transitions."""

import pytest
from app.database import create_recording_job, get_or_create_user, get_recording_job, update_job_status
from app.models import JobState


def test_all_fourteen_states_exist():
    """Explicitly verify that all 14 states from the specification are defined."""
    required_states = {
        "IDLE",
        "VALIDATING",
        "QUEUED",
        "STARTING_BROWSER",
        "JOINING_MEETING",
        "WAITING_FOR_AUTHORIZATION",
        "WAITING_FOR_HOST",
        "PREPARING_CAPTURE",
        "RECORDING",
        "STOPPING",
        "UPLOADING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
    actual_states = {s.value for s in JobState}
    assert required_states == actual_states
    assert len(JobState) == 14


@pytest.mark.asyncio
async def test_full_state_transition_sequence():
    """Verify progressive state updates with timestamps and detail messages."""
    user = await get_or_create_user(telegram_id=1001)

    job = await create_recording_job(
        user_id=user.id,
        job_id="REC-STATE-FLOW",
        zoom_url="https://app.zoom.us/wc/join/999888777",
        meeting_id="999888777",
        passcode="pwd123",
        display_name="Zoom Bot",
    )
    assert job.status == JobState.QUEUED

    # 1. STARTING_BROWSER
    await update_job_status(job.job_id, JobState.STARTING_BROWSER, "Chromium launched")
    j = await get_recording_job(job.job_id)
    assert j.status == JobState.STARTING_BROWSER
    assert j.status_detail == "Chromium launched"

    # 2. JOINING_MEETING
    await update_job_status(job.job_id, JobState.JOINING_MEETING, "Connecting to web client")
    j = await get_recording_job(job.job_id)
    assert j.status == JobState.JOINING_MEETING

    # 3. WAITING_FOR_HOST
    await update_job_status(job.job_id, JobState.WAITING_FOR_HOST, "In waiting room")
    j = await get_recording_job(job.job_id)
    assert j.status == JobState.WAITING_FOR_HOST

    # 4. PREPARING_CAPTURE
    await update_job_status(job.job_id, JobState.PREPARING_CAPTURE, "Setting up capture pipeline")
    j = await get_recording_job(job.job_id)
    assert j.status == JobState.PREPARING_CAPTURE

    # 5. RECORDING
    await update_job_status(job.job_id, JobState.RECORDING, "Active tab recording")
    j = await get_recording_job(job.job_id)
    assert j.status == JobState.RECORDING
    assert j.started_at is not None

    # 6. STOPPING
    await update_job_status(job.job_id, JobState.STOPPING, "Finalizing media")
    j = await get_recording_job(job.job_id)
    assert j.status == JobState.STOPPING
    assert j.stopped_at is not None

    # 7. UPLOADING
    await update_job_status(job.job_id, JobState.UPLOADING, "Sending to cloud storage")
    j = await get_recording_job(job.job_id)
    assert j.status == JobState.UPLOADING

    # 8. COMPLETED
    await update_job_status(job.job_id, JobState.COMPLETED, "Recording ready")
    j = await get_recording_job(job.job_id)
    assert j.status == JobState.COMPLETED
    assert j.completed_at is not None
