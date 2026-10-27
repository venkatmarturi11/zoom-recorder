"""Standalone Zoom Meeting Attendee Runner.

Allows running the Zoom attendee independently from the command line:
Usage:
    python scripts/attend_meeting.py --url "https://zoom.us/j/123456789" --name "Bot Name" [--passcode "123"] [--duration 3600] [--headful]
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.logging_config import setup_logging
from app.models import JobState
from app.security import validate_and_parse_zoom_url
from app.zoom_integration import ZoomAttendee

logger = setup_logging()


async def main():
    parser = argparse.ArgumentParser(description="Standalone Zoom Meeting Attendee")
    parser.add_argument("--url", "-u", required=True, help="Zoom meeting URL")
    parser.add_argument("--name", "-n", default="Zoom Attendee Assistant", help="Participant display name")
    parser.add_argument("--passcode", "-p", default=None, help="Meeting passcode (optional)")
    parser.add_argument("--duration", "-d", type=int, default=7200, help="Max attendance duration in seconds")
    parser.add_argument("--headful", action="store_true", help="Launch visible browser (non-headless)")

    args = parser.parse_args()

    # Validate Zoom URL
    try:
        norm_url, mid, extracted_pwd = validate_and_parse_zoom_url(args.url)
        passcode = args.passcode or extracted_pwd
        logger.info("Validated Zoom URL. Meeting ID: %s", mid)
    except Exception as exc:
        logger.error("URL validation error: %s", exc)
        sys.exit(1)

    # Configure headless
    settings = get_settings()
    if args.headful:
        settings.browser_headless = False

    logger.info("Initializing Zoom Attendee: '%s'...", args.name)
    attendee = ZoomAttendee(display_name=args.name, headless=settings.browser_headless)

    # Graceful shutdown handling
    stop_event = asyncio.Event()

    def _sig_handler():
        logger.info("Stop requested. Leaving meeting...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig_handler)
        except NotImplementedError:
            pass

    try:
        logger.info("Joining Zoom meeting: %s", norm_url.split("?")[0])
        initial_state = await attendee.join(norm_url, passcode=passcode)
        logger.info("Landing state: %s", initial_state.value)

        if initial_state in (JobState.RECORDING, JobState.PREPARING_CAPTURE):
            logger.info("Successfully attended meeting! Sitting in meeting...")
        elif initial_state == JobState.WAITING_FOR_HOST:
            logger.info("Currently in Zoom waiting room. Waiting for host...")
        elif initial_state == JobState.WAITING_FOR_AUTHORIZATION:
            logger.warning("Meeting requires sign-in, registration, or manual host authorization.")
        elif initial_state == JobState.COMPLETED:
            logger.info("Meeting has already concluded.")
            return

        # Stay in meeting until duration expires or stop requested
        elapsed = 0
        poll_step = 5
        while elapsed < args.duration and not stop_event.is_set():
            await asyncio.sleep(poll_step)
            elapsed += poll_step

            if not attendee.browser_manager.is_running or not attendee.integrator:
                logger.warning("Browser closed.")
                break

            if await attendee.integrator.check_meeting_ended():
                logger.info("Meeting was ended by host.")
                break

            await attendee.integrator.keep_alive_poll()

    except Exception as exc:
        logger.error("Error attending meeting: %s", exc, exc_info=True)
    finally:
        logger.info("Closing Zoom Attendee...")
        await attendee.leave()
        logger.info("Zoom Attendee exited cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
