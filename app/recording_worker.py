"""Dedicated browser recording worker executing the complete meeting capture pipeline."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.auth import PersistentZoomSessionManager
from app.browser_manager import BrowserManager
from app.capture_manager import TabCaptureManager
from app.config import get_settings
from app.database import (
    create_recording_metadata,
    update_job_status,
    update_recording_upload_status,
)
from app.media_processor import MediaProcessor
from app.models import JobState, RecordingJob
from app.notifications import NotificationService
from app.storage import get_storage_provider
from app.zoom_integration import ZoomMeetingIntegrator

logger = logging.getLogger("zoom_recorder.worker")


class RecordingWorker:
    """Coordinates browser navigation, tab media capture, finalization, and external upload."""

    def __init__(self, job: RecordingJob, telegram_chat_id: int, notifier: Optional[NotificationService] = None):
        self.job = job
        self.telegram_chat_id = telegram_chat_id
        self.notifier = notifier
        self.settings = get_settings()

        self.browser_manager = BrowserManager()
        self.capture_manager = TabCaptureManager()
        self.media_processor = MediaProcessor()
        self.storage_provider = get_storage_provider()
        self.session_manager = PersistentZoomSessionManager()

        self._stop_event = asyncio.Event()
        self._is_active = False

    def request_stop(self) -> None:
        """Signal the recording worker to gracefully stop capturing and finalize."""
        logger.info("Graceful stop requested for job %s", self.job.job_id)
        self._stop_event.set()

    async def run(self) -> None:
        """Execute the full recording workflow from browser launch to external storage upload."""
        self._is_active = True
        raw_recording_path: Optional[Path] = None
        final_mp4_path: Optional[Path] = None
        recording_id = f"REC-MED-{uuid.uuid4().hex[:8].upper()}"

        try:
            # 1. State: STARTING_BROWSER
            await self._update_state(JobState.STARTING_BROWSER, "Launching Chromium environment...")
            saved_session = self.session_manager.load_session_state(self.job.user_id)
            page = await self.browser_manager.launch_browser(storage_state=saved_session)

            # 2. State: JOINING_MEETING
            await self._update_state(JobState.JOINING_MEETING, "Opening Zoom web client and verifying access...")
            zoom = ZoomMeetingIntegrator(page)

            detected_state = await zoom.navigate_and_join(
                join_url=self.job.zoom_url,
                display_name=self.job.display_name,
                passcode=self.job.passcode,
            )

            # Handle Waiting Room or Authorization Required
            if detected_state == JobState.WAITING_FOR_HOST:
                await self._update_state(
                    JobState.WAITING_FOR_HOST,
                    "In Zoom waiting room. Waiting for meeting host to admit participant...",
                )
                # Wait up to 10 minutes in waiting room before timing out
                admitted = await self._wait_for_host_admittance(zoom, max_wait_seconds=600)
                if not admitted:
                    raise TimeoutError("Host did not admit the recording assistant within 10 minutes.")

            elif detected_state == JobState.WAITING_FOR_AUTHORIZATION:
                await self._update_state(
                    JobState.WAITING_FOR_AUTHORIZATION,
                    "Meeting requires registration, sign-in, or host authorization.",
                )
                raise PermissionError("Access restricted: Registration or host authentication required.")

            # 3. State: PREPARING_CAPTURE & RECORDING
            await self._update_state(JobState.PREPARING_CAPTURE, "Configuring audio/video tab capture...")
            timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            raw_filename = f"{self.job.job_id}_{timestamp_str}_raw.mp4"
            raw_recording_path = self.settings.local_storage_dir / raw_filename

            await self.capture_manager.start_capture(raw_recording_path)
            await self._update_state(JobState.RECORDING, "Active meeting tab recording in progress.")

            # Save updated storage cookies/session if successful
            session_state = await self.browser_manager.get_storage_state()
            if session_state:
                self.session_manager.save_session_state(self.job.user_id, session_state)

            # 4. Sustained Recording Loop
            # Runs until stop requested, meeting ends, browser closes, or safety timeout
            max_duration = self.settings.max_recording_duration_seconds
            poll_interval = 3
            elapsed = 0

            while not self._stop_event.is_set() and elapsed < max_duration:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                # Verify browser is still healthy
                if not self.browser_manager.is_running:
                    logger.warning("Browser closed unexpectedly during recording.")
                    break

                # Check if host ended the meeting
                if await zoom.check_meeting_ended():
                    logger.info("Zoom meeting ended by host.")
                    break

            # 5. State: STOPPING
            await self._update_state(JobState.STOPPING, "Finalizing and packaging media streams...")
            await self.capture_manager.stop_capture()
            await self.browser_manager.close_browser()

            # 6. Media Validation & MP4 Remuxing
            final_filename = f"{self.job.job_id}_{timestamp_str}.mp4"
            final_mp4_path = self.settings.local_storage_dir / final_filename

            if not raw_recording_path.exists() or raw_recording_path.stat().st_size == 0:
                raise FileNotFoundError("Raw recording file was not generated or is 0 bytes.")

            final_mp4_path = await self.media_processor.finalize_mp4(raw_recording_path, final_mp4_path)
            probe_info = await self.media_processor.probe_file(final_mp4_path)

            if not probe_info["is_valid"]:
                raise ValueError("Generated MP4 file failed validation checks.")

            # 7. State: UPLOADING
            await self._update_state(JobState.UPLOADING, "Uploading recording to external storage...")
            storage_key = f"recordings/{final_filename}"
            await self.storage_provider.upload_file(final_mp4_path, storage_key)
            download_url = await self.storage_provider.get_download_url(storage_key)

            # 8. Record Metadata & State: COMPLETED
            rec_meta = await create_recording_metadata(
                recording_id=recording_id,
                job_id=self.job.job_id,
                user_id=self.job.user_id,
                file_path=str(final_mp4_path) if not self.settings.cleanup_local_files_after_upload else None,
                storage_key=storage_key,
                storage_provider=self.settings.storage_provider,
                duration_seconds=probe_info["duration"],
                file_size_bytes=probe_info["size_bytes"],
                video_format=probe_info["format"],
                resolution=probe_info["resolution"],
                upload_status="UPLOADED",
                download_url=download_url,
            )

            await self._update_state(JobState.COMPLETED, "Recording successfully uploaded and available.")

            if self.notifier:
                await self.notifier.notify_recording_completed(
                    chat_id=self.telegram_chat_id,
                    job_id=self.job.job_id,
                    recording_id=recording_id,
                    duration_seconds=probe_info["duration"],
                    file_size_bytes=probe_info["size_bytes"],
                    download_url=download_url,
                )

            # 9. Clean up temporary local files if configured
            if self.settings.cleanup_local_files_after_upload and self.settings.storage_provider == "s3":
                if raw_recording_path and raw_recording_path.exists():
                    raw_recording_path.unlink()
                if final_mp4_path and final_mp4_path.exists():
                    final_mp4_path.unlink()
                logger.info("Cleaned up local temporary video files after external S3 upload.")

        except Exception as exc:
            logger.error("Job %s execution failed: %s", self.job.job_id, exc, exc_info=True)
            await self._update_state(JobState.FAILED, f"Recording failed: {str(exc)}", error_message=str(exc))
        finally:
            self._is_active = False
            # Ensure browser and capture processes are terminated
            try:
                await self.capture_manager.stop_capture()
            except Exception:
                pass
            try:
                await self.browser_manager.close_browser()
            except Exception:
                pass

    async def _update_state(self, state: JobState, detail: str, error_message: Optional[str] = None) -> None:
        """Update job state in DB and dispatch Telegram notification."""
        self.job.status = state
        self.job.status_detail = detail
        if error_message:
            self.job.error_message = error_message

        await update_job_status(self.job.job_id, state, detail=detail, error_message=error_message)

        if self.notifier:
            await self.notifier.notify_job_status(self.telegram_chat_id, self.job, state, detail)

    async def _wait_for_host_admittance(self, zoom: ZoomMeetingIntegrator, max_wait_seconds: int = 600) -> bool:
        """Poll page to detect when participant is admitted from the waiting room."""
        elapsed = 0
        step = 5
        while elapsed < max_wait_seconds and not self._stop_event.is_set():
            await asyncio.sleep(step)
            elapsed += step
            current_state = await zoom.evaluate_meeting_state(max_wait_seconds=5)
            if current_state in (JobState.RECORDING, JobState.PREPARING_CAPTURE):
                logger.info("Admitted to meeting from waiting room!")
                return True
        return False
