"""Job manager coordinating recording jobs, workers, queues, and concurrency limits."""

import asyncio
import logging
from typing import Dict, Optional, Tuple
from sqlalchemy import func, select

import app.database as db
from app.config import get_settings
from app.models import JobState, RecordingJob, User
from app.notifications import NotificationService
from app.recording_worker import RecordingWorker

logger = logging.getLogger("zoom_recorder.job_manager")


class JobManager:
    """Central coordinator for scheduling, tracking, stopping, and cancelling recording jobs."""

    def __init__(self, notifier: Optional[NotificationService] = None):
        self.notifier = notifier
        self.settings = get_settings()
        self._active_workers: Dict[str, RecordingWorker] = {}
        self._worker_tasks: Dict[str, asyncio.Task] = {}

    def set_notifier(self, notifier: NotificationService) -> None:
        self.notifier = notifier

    async def startup(self) -> None:
        """Initialize database, reset orphaned jobs from previous crashes."""
        orphaned_count = await db.mark_orphaned_jobs_as_failed()
        if orphaned_count > 0:
            logger.warning("Cleaned up %d orphaned job(s) from previous worker instance.", orphaned_count)

    async def generate_job_id(self) -> str:
        """Generate human-readable sequential job ID (e.g., REC-1001)."""
        async with db.async_session_factory() as session:
            count_stmt = select(func.count(RecordingJob.id))
            count = (await session.execute(count_stmt)).scalar() or 0
            return f"REC-{1001 + count}"

    async def create_and_start_job(
        self,
        user: User,
        telegram_chat_id: int,
        zoom_url: str,
        meeting_id: str,
        passcode: Optional[str] = None,
        display_name_override: Optional[str] = None,
    ) -> RecordingJob:
        """Validate concurrency, persist job record, and launch background recording worker."""
        # 1. Check if user already has an active recording
        existing_job = await db.get_active_job_for_user(user.id)
        if existing_job:
            raise ValueError(
                f"You already have an active recording in progress (Job ID: {existing_job.job_id}). "
                "Use /status to check progress or /stop to finalize it."
            )

        # 2. Check global concurrency limit
        if len(self._active_workers) >= self.settings.max_concurrent_jobs:
            raise ValueError(
                f"Worker capacity reached ({self.settings.max_concurrent_jobs} concurrent job max). "
                "Please wait for active recordings to complete."
            )

        # Use user-specified one-time name > override > default
        display_name = (
            display_name_override
            or user.custom_display_name
            or self.settings.bot_display_name
        )

        job_id = await self.generate_job_id()
        job = await db.create_recording_job(
            user_id=user.id,
            job_id=job_id,
            zoom_url=zoom_url,
            meeting_id=meeting_id,
            passcode=passcode,
            display_name=display_name,
        )

        # Spawn background recording worker
        worker = RecordingWorker(
            job=job,
            telegram_chat_id=telegram_chat_id,
            notifier=self.notifier,
        )
        self._active_workers[job.job_id] = worker

        task = asyncio.create_task(self._run_worker_wrapper(job.job_id, worker))
        self._worker_tasks[job.job_id] = task

        logger.info("Created and launched recording job %s for meeting %s", job.job_id, meeting_id)
        return job

    async def _run_worker_wrapper(self, job_id: str, worker: RecordingWorker) -> None:
        """Run worker and cleanly remove from active worker list upon completion."""
        try:
            await worker.run()
        finally:
            self._active_workers.pop(job_id, None)
            self._worker_tasks.pop(job_id, None)

    async def stop_job(self, job_id: str, user_id: int) -> Tuple[bool, str]:
        """Signal an active job to gracefully finalize and upload, or cancel pre-recording state."""
        job = await db.get_recording_job(job_id)
        if not job:
            return False, f"Job {job_id} was not found. The recording session may have concluded or the database was restarted."

        user = await db.get_user_by_id(user_id)
        is_admin = bool(user and user.is_admin)

        if job.user_id != user_id and not is_admin:
            return False, f"Access denied: Job {job_id} belongs to another user."

        if job.status in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
            return False, f"Job {job.job_id} has already concluded (Current status: {job.status.value})."

        worker = self._active_workers.get(job_id)
        if worker:
            worker.request_stop()
            if job.status == JobState.RECORDING:
                return True, "Stop signal received. Packaging and uploading MP4..."
            else:
                return True, f"Session stopped while in state '{job.status.value}'."

        if job.status == JobState.RECORDING:
            await db.update_job_status(job_id, JobState.STOPPING, "Stop signal received.")
            return True, "Recording stopping. Finalizing media..."

        if job.status in (JobState.STARTING_BROWSER, JobState.JOINING_MEETING, JobState.WAITING_FOR_HOST, JobState.QUEUED):
            await db.update_job_status(job_id, JobState.CANCELLED, "Stopped by user before recording.")
            task = self._worker_tasks.get(job_id)
            if task and not task.done():
                task.cancel()
            self._active_workers.pop(job_id, None)
            return True, "Meeting session stopped and cancelled by user."

        return False, f"Job has already concluded (Current status: {job.status.value})."

    async def cancel_job(self, job_id: str, user_id: int) -> Tuple[bool, str]:
        """Cancel a queued job that has not yet started recording."""
        job = await db.get_recording_job(job_id)
        if not job:
            return False, f"Job {job_id} was not found. The recording session may have concluded or the database was restarted."

        user = await db.get_user_by_id(user_id)
        is_admin = bool(user and user.is_admin)

        if job.user_id != user_id and not is_admin:
            return False, f"Access denied: Job {job_id} belongs to another user."

        # Cannot cancel if recording has already started producing files
        non_cancellable_states = [
            JobState.RECORDING,
            JobState.STOPPING,
            JobState.UPLOADING,
            JobState.COMPLETED,
        ]
        if job.status in non_cancellable_states:
            return False, f"Job cannot be cancelled in state '{job.status.value}'. Use /stop instead."

        if job.status in (JobState.FAILED, JobState.CANCELLED):
            return False, f"Job has already concluded ({job.status.value})."

        # Signal worker and cancel task
        worker = self._active_workers.get(job_id)
        if worker:
            worker.request_stop()
        task = self._worker_tasks.get(job_id)
        if task and not task.done():
            task.cancel()

        self._active_workers.pop(job_id, None)
        self._worker_tasks.pop(job_id, None)

        await db.update_job_status(job_id, JobState.CANCELLED, "Cancelled by user request.")
        return True, "Job has been cancelled."

    def get_active_worker_count(self) -> int:
        return len(self._active_workers)
