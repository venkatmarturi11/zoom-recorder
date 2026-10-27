"""Asynchronous database connectivity and repository functions."""

from datetime import datetime, timezone
from typing import AsyncGenerator, List, Optional
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import desc, func, select, text, update

from app.config import get_settings
from app.models import (
    Base,
    JobState,
    Recording,
    RecordingJob,
    User,
    WorkerHeartbeat,
    utc_now,
)

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create all database tables if they do not exist and ensure schema integrity."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Gracefully add custom columns if migrating an existing database
        for col_def in [
            "custom_display_name VARCHAR(128)",
            "zoom_email VARCHAR(256)",
            "zoom_connected_at DATETIME",
            "zoom_session_status VARCHAR(32) DEFAULT 'DISCONNECTED'",
        ]:
            try:
                col_name = col_def.split()[0]
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_def}"))
            except Exception:
                pass  # Column already exists or fresh database


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency / context manager generator for async sessions."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# User Repository
# ---------------------------------------------------------------------------

async def get_or_create_user(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> User:
    """Fetch existing user by Telegram ID or create a new user record."""
    settings = get_settings()
    is_allowed = (
        not settings.allowed_telegram_user_ids  # If list is empty, open access or initial setup
        or telegram_id in settings.allowed_telegram_user_ids
        or telegram_id in settings.admin_user_ids
    )
    is_admin = telegram_id in settings.admin_user_ids

    async with async_session_factory() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                is_admin=is_admin,
                is_allowed=is_allowed,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        else:
            # Update user info if changed
            updated = False
            if user.username != username:
                user.username = username
                updated = True
            if user.first_name != first_name:
                user.first_name = first_name
                updated = True
            if user.is_admin != is_admin:
                user.is_admin = is_admin
                updated = True
            if user.is_allowed != is_allowed:
                user.is_allowed = is_allowed
                updated = True
            if updated:
                await session.commit()
                await session.refresh(user)
        return user


async def set_user_display_name(telegram_id: int, display_name: str) -> Optional[User]:
    """Save the user's custom one-time configured Zoom bot participant name."""
    async with async_session_factory() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.custom_display_name = display_name.strip()
            await session.commit()
            await session.refresh(user)
        return user


async def update_user_zoom_session(
    telegram_id: int,
    zoom_email: Optional[str] = None,
    status: str = "CONNECTED",
) -> Optional[User]:
    """Update user's Zoom account session status and masked email."""
    async with async_session_factory() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.zoom_session_status = status
            if zoom_email is not None:
                user.zoom_email = zoom_email
            if status == "CONNECTED":
                user.zoom_connected_at = utc_now()
            await session.commit()
            await session.refresh(user)
        return user


async def get_user_zoom_status(telegram_id: int) -> dict:
    """Retrieve user's Zoom account status, masked email, and connection timestamp."""
    async with async_session_factory() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            return {
                "status": "DISCONNECTED",
                "zoom_email": None,
                "connected_at": None,
            }
        return {
            "status": user.zoom_session_status or "DISCONNECTED",
            "zoom_email": user.zoom_email,
            "connected_at": user.zoom_connected_at,
        }


async def disconnect_user_zoom(telegram_id: int) -> Optional[User]:
    """Mark user's Zoom session as disconnected and clear account email."""
    async with async_session_factory() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.zoom_session_status = "DISCONNECTED"
            user.zoom_email = None
            user.zoom_connected_at = None
            await session.commit()
            await session.refresh(user)
        return user


# ---------------------------------------------------------------------------
# Recording Job Repository
# ---------------------------------------------------------------------------

async def create_recording_job(
    user_id: int,
    job_id: str,
    zoom_url: str,
    meeting_id: str,
    passcode: Optional[str],
    display_name: str,
) -> RecordingJob:
    """Create a new pending recording job."""
    async with async_session_factory() as session:
        job = RecordingJob(
            job_id=job_id,
            user_id=user_id,
            zoom_url=zoom_url,
            meeting_id=meeting_id,
            passcode=passcode,
            display_name=display_name,
            status=JobState.QUEUED,
            status_detail="Recording job queued for browser worker",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def get_recording_job(job_id: str) -> Optional[RecordingJob]:
    """Retrieve recording job by unique job_id."""
    async with async_session_factory() as session:
        stmt = select(RecordingJob).where(RecordingJob.job_id == job_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_active_job_for_user(user_id: int) -> Optional[RecordingJob]:
    """Get the currently active recording job for a given user if one exists."""
    active_states = [
        JobState.VALIDATING,
        JobState.QUEUED,
        JobState.STARTING_BROWSER,
        JobState.JOINING_MEETING,
        JobState.WAITING_FOR_AUTHORIZATION,
        JobState.WAITING_FOR_HOST,
        JobState.PREPARING_CAPTURE,
        JobState.RECORDING,
        JobState.STOPPING,
        JobState.UPLOADING,
    ]
    async with async_session_factory() as session:
        stmt = (
            select(RecordingJob)
            .where(
                RecordingJob.user_id == user_id,
                RecordingJob.status.in_(active_states),
            )
            .order_by(desc(RecordingJob.created_at))
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def update_job_status(
    job_id: str,
    status: JobState,
    detail: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Update job status and set lifecycle timestamps appropriately."""
    async with async_session_factory() as session:
        stmt = select(RecordingJob).where(RecordingJob.job_id == job_id)
        result = await session.execute(stmt)
        job = result.scalar_one_or_none()
        if not job:
            return

        job.status = status
        if detail:
            job.status_detail = detail
        if error_message:
            job.error_message = error_message

        now = utc_now()
        if status == JobState.RECORDING and not job.started_at:
            job.started_at = now
        elif status == JobState.STOPPING and not job.stopped_at:
            job.stopped_at = now
        elif status in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
            job.completed_at = now

        await session.commit()


async def mark_orphaned_jobs_as_failed() -> int:
    """On worker startup, mark any unfinalized jobs from prior runs as FAILED."""
    incomplete_states = [
        JobState.STARTING_BROWSER,
        JobState.JOINING_MEETING,
        JobState.WAITING_FOR_AUTHORIZATION,
        JobState.WAITING_FOR_HOST,
        JobState.PREPARING_CAPTURE,
        JobState.RECORDING,
        JobState.STOPPING,
        JobState.UPLOADING,
    ]
    async with async_session_factory() as session:
        stmt = (
            update(RecordingJob)
            .where(RecordingJob.status.in_(incomplete_states))
            .values(
                status=JobState.FAILED,
                error_message="Worker restarted during recording execution",
                completed_at=utc_now(),
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount


# ---------------------------------------------------------------------------
# Recording Metadata Repository
# ---------------------------------------------------------------------------

async def create_recording_metadata(
    recording_id: str,
    job_id: str,
    user_id: int,
    file_path: Optional[str],
    storage_key: str,
    storage_provider: str,
    duration_seconds: Optional[float] = None,
    file_size_bytes: Optional[int] = None,
    video_format: str = "mp4",
    resolution: str = "1280x720",
    upload_status: str = "PENDING",
    download_url: Optional[str] = None,
) -> Recording:
    """Create metadata record for a saved recording."""
    async with async_session_factory() as session:
        rec = Recording(
            recording_id=recording_id,
            job_id=job_id,
            user_id=user_id,
            file_path=file_path,
            storage_key=storage_key,
            storage_provider=storage_provider,
            duration_seconds=duration_seconds,
            file_size_bytes=file_size_bytes,
            video_format=video_format,
            resolution=resolution,
            upload_status=upload_status,
            download_url=download_url,
        )
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        return rec


async def get_recording(recording_id: str) -> Optional[Recording]:
    """Retrieve recording by recording_id."""
    async with async_session_factory() as session:
        stmt = select(Recording).where(Recording.recording_id == recording_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_recordings_for_user(user_id: int, limit: int = 15) -> List[Recording]:
    """Retrieve history of recordings for a specific user."""
    async with async_session_factory() as session:
        stmt = (
            select(Recording)
            .where(Recording.user_id == user_id)
            .order_by(desc(Recording.created_at))
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def update_recording_upload_status(
    recording_id: str,
    upload_status: str,
    download_url: Optional[str] = None,
) -> None:
    """Update upload status and download link."""
    async with async_session_factory() as session:
        stmt = select(Recording).where(Recording.recording_id == recording_id)
        result = await session.execute(stmt)
        rec = result.scalar_one_or_none()
        if rec:
            rec.upload_status = upload_status
            if download_url:
                rec.download_url = download_url
            await session.commit()


# ---------------------------------------------------------------------------
# Worker & System Health
# ---------------------------------------------------------------------------

async def update_worker_heartbeat(
    worker_id: str,
    active_jobs: int,
    cpu_percent: Optional[float] = None,
    memory_percent: Optional[float] = None,
) -> None:
    """Upsert worker heartbeat state."""
    async with async_session_factory() as session:
        stmt = select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id)
        result = await session.execute(stmt)
        hb = result.scalar_one_or_none()
        if not hb:
            hb = WorkerHeartbeat(
                worker_id=worker_id,
                active_jobs_count=active_jobs,
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
            )
            session.add(hb)
        else:
            hb.active_jobs_count = active_jobs
            hb.last_heartbeat = utc_now()
            hb.cpu_percent = cpu_percent
            hb.memory_percent = memory_percent
        await session.commit()


async def get_system_health_stats() -> dict:
    """Collect overall system stats for the /health command."""
    async with async_session_factory() as session:
        total_jobs_stmt = select(func.count(RecordingJob.id))
        total_jobs = (await session.execute(total_jobs_stmt)).scalar() or 0

        active_jobs_stmt = select(func.count(RecordingJob.id)).where(
            RecordingJob.status.in_([
                JobState.STARTING_BROWSER,
                JobState.JOINING_MEETING,
                JobState.RECORDING,
                JobState.STOPPING,
                JobState.UPLOADING,
            ])
        )
        active_jobs = (await session.execute(active_jobs_stmt)).scalar() or 0

        total_recordings_stmt = select(func.count(Recording.id))
        total_recordings = (await session.execute(total_recordings_stmt)).scalar() or 0

        return {
            "total_jobs": total_jobs,
            "active_jobs": active_jobs,
            "total_recordings": total_recordings,
        }
