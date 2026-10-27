"""Automated tests for Zoom Web Client state parsing and UI detection logic."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from app.models import JobState
from app.zoom_integration import ZoomMeetingIntegrator


@pytest.mark.asyncio
async def test_waiting_room_detection():
    """Verify waiting room text triggers WAITING_FOR_HOST state."""
    mock_page = MagicMock()
    mock_page.content = AsyncMock(
        return_value="<html><body><div>Please wait, the meeting host will let you in soon</div></body></html>"
    )

    integrator = ZoomMeetingIntegrator(mock_page)
    state = await integrator.evaluate_meeting_state(max_wait_seconds=1)
    assert state == JobState.WAITING_FOR_HOST


@pytest.mark.asyncio
async def test_registration_required_detection():
    """Verify registration/auth required text triggers WAITING_FOR_AUTHORIZATION state."""
    mock_page = MagicMock()
    mock_page.content = AsyncMock(
        return_value="<html><body><div>This meeting requires registration to join</div></body></html>"
    )

    integrator = ZoomMeetingIntegrator(mock_page)
    state = await integrator.evaluate_meeting_state(max_wait_seconds=1)
    assert state == JobState.WAITING_FOR_AUTHORIZATION


@pytest.mark.asyncio
async def test_meeting_ended_detection():
    """Verify meeting ended text returns COMPLETED state."""
    mock_page = MagicMock()
    mock_page.content = AsyncMock(
        return_value="<html><body><div>This meeting has been ended by the host.</div></body></html>"
    )

    integrator = ZoomMeetingIntegrator(mock_page)
    state = await integrator.evaluate_meeting_state(max_wait_seconds=1)
    assert state == JobState.COMPLETED
    assert await integrator.check_meeting_ended() is True


@pytest.mark.asyncio
async def test_stop_event_cancels_navigation():
    """Verify stop_event interrupts navigation immediately."""
    import asyncio
    mock_page = MagicMock()
    integrator = ZoomMeetingIntegrator(mock_page)

    stop_event = asyncio.Event()
    stop_event.set()

    state = await integrator.navigate_and_join(
        join_url="https://zoom.us/wc/123/join",
        display_name="TestBot",
        stop_event=stop_event,
    )
    assert state == JobState.CANCELLED


@pytest.mark.asyncio
async def test_invalid_link_raises_zoom_navigation_error():
    """Verify invalid meeting link error raises ZoomNavigationError."""
    from app.zoom_integration import ZoomNavigationError
    mock_page = MagicMock()
    mock_page.title = AsyncMock(return_value="Error - Zoom")
    mock_page.inner_text = AsyncMock(return_value="This meeting link is invalid (3,001)")

    integrator = ZoomMeetingIntegrator(mock_page)
    with pytest.raises(ZoomNavigationError, match="3,001"):
        await integrator._check_immediate_errors()

