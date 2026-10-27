"""Automated tests for Zoom Web Client state parsing and UI detection logic."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from app.models import JobState
from app.zoom_integration import ZoomMeetingIntegrator, ZoomRegistrationRequiredError


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
    """Verify webinar/meeting registration triggers ZoomRegistrationRequiredError."""
    mock_page = MagicMock()
    mock_page.content = AsyncMock(
        return_value="<html><body><div>This meeting requires registration to join</div></body></html>"
    )

    integrator = ZoomMeetingIntegrator(mock_page)
    with pytest.raises(ZoomRegistrationRequiredError):
        await integrator.evaluate_meeting_state(max_wait_seconds=1)


@pytest.mark.asyncio
async def test_signin_required_detection():
    """Verify sign in required text triggers WAITING_FOR_AUTHORIZATION state."""
    mock_page = MagicMock()
    mock_page.content = AsyncMock(
        return_value="<html><body><div>Please sign in to join this meeting</div></body></html>"
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


@pytest.mark.asyncio
async def test_passcode_required_raises_zoom_access_error():
    """Verify missing passcode when required raises ZoomAccessError."""
    from app.zoom_integration import ZoomAccessError
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.title = AsyncMock(return_value="Join Meeting - Zoom")
    mock_page.inner_text = AsyncMock(return_value="")
    mock_page.evaluate = AsyncMock(return_value=None)

    # Passcode field locator returns is_visible=True
    mock_pwd_locator = MagicMock()
    mock_pwd_locator.is_visible = AsyncMock(return_value=True)

    def mock_locator(sel):
        loc = MagicMock()
        if "pwd" in sel or "passcode" in sel:
            loc.first = mock_pwd_locator
        else:
            loc_first = MagicMock()
            loc_first.is_visible = AsyncMock(return_value=False)
            loc.first = loc_first
        return loc

    mock_page.locator = mock_locator
    integrator = ZoomMeetingIntegrator(mock_page)

    with pytest.raises(ZoomAccessError, match="requires a passcode"):
        await integrator.navigate_and_join(
            join_url="https://app.zoom.us/wc/join/83373006475",
            display_name="TestBot",
            passcode=None,
        )


@pytest.mark.asyncio
async def test_incorrect_passcode_raises_zoom_access_error():
    """Verify incorrect passcode message raises ZoomAccessError during state evaluation."""
    from app.zoom_integration import ZoomAccessError
    mock_page = MagicMock()
    mock_page.inner_text = AsyncMock(return_value="Incorrect password. Please try again.")

    mock_err_loc = MagicMock()
    mock_err_loc.is_visible = AsyncMock(return_value=True)
    mock_err_loc.inner_text = AsyncMock(return_value="Incorrect Password")

    def mock_locator(sel):
        loc = MagicMock()
        if "error" in sel:
            loc.first = mock_err_loc
        else:
            loc_first = MagicMock()
            loc_first.is_visible = AsyncMock(return_value=False)
            loc.first = loc_first
        return loc

    mock_page.locator = mock_locator
    integrator = ZoomMeetingIntegrator(mock_page)

    with pytest.raises(ZoomAccessError, match="Incorrect Zoom meeting passcode"):
        await integrator.evaluate_meeting_state(max_wait_seconds=2)


@pytest.mark.asyncio
async def test_prejoin_guard_prevents_false_recording():
    """Verify preview screen elements prevent premature RECORDING state."""
    from app.zoom_integration import ZoomNavigationError
    mock_page = MagicMock()
    mock_page.inner_text = AsyncMock(return_value="Enter your name and join")
    mock_page.content = AsyncMock(return_value="<html><body>preview</body></html>")
    mock_page.evaluate = AsyncMock(return_value=None)

    # Pre-join form is visible
    mock_prejoin_loc = MagicMock()
    mock_prejoin_loc.is_visible = AsyncMock(return_value=True)

    def mock_locator(sel):
        loc = MagicMock()
        if "input-for-name" in sel or "preview-join-button" in sel:
            loc.first = mock_prejoin_loc
        else:
            loc_first = MagicMock()
            loc_first.is_visible = AsyncMock(return_value=False)
            loc.first = loc_first
        return loc

    mock_page.locator = mock_locator
    integrator = ZoomMeetingIntegrator(mock_page)

    with pytest.raises(ZoomNavigationError, match="Stuck on pre-join preview screen"):
        await integrator.evaluate_meeting_state(max_wait_seconds=2)


@pytest.mark.asyncio
async def test_prejoin_loop_detects_passcode_requirement():
    """Verify that if passcode input appears during pre-join loop, ZoomAccessError is raised."""
    from app.zoom_integration import ZoomAccessError
    mock_page = MagicMock()
    mock_page.inner_text = AsyncMock(return_value="Meeting Passcode Your Name Join")
    mock_page.content = AsyncMock(return_value="<html><body>Meeting Passcode</body></html>")
    mock_page.evaluate = AsyncMock(return_value=None)

    # Passcode field is visible
    mock_pwd_loc = MagicMock()
    mock_pwd_loc.is_visible = AsyncMock(return_value=True)

    def mock_locator(sel):
        loc = MagicMock()
        if "pwd" in sel or "passcode" in sel or "input-for-name" in sel or "preview-join-button" in sel:
            loc.first = mock_pwd_loc
        else:
            loc_first = MagicMock()
            loc_first.is_visible = AsyncMock(return_value=False)
            loc.first = loc_first
        return loc

    mock_page.locator = mock_locator
    integrator = ZoomMeetingIntegrator(mock_page)

    with pytest.raises(ZoomAccessError, match="requires a passcode"):
        await integrator.evaluate_meeting_state(
            display_name="TestBot",
            passcode=None,
            max_wait_seconds=4,
        )


