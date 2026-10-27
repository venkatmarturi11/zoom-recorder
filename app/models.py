"""SQLAlchemy models and state enumerations for recording operations."""

import enum
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class JobState(str, enum.Enum):
    """The 14 explicit states representing the full recording lifecycle."""
    IDLE = "IDLE"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    STARTING_BROWSER = "STARTING_BROWSER"
    JOINING_MEETING = "JOINING_MEETING"
    WAITING_FOR_AUTHORIZATION = "WAITING_FOR_AUTHORIZATION"
    WAITING_FOR_HOST = "WAITING_FOR_HOST"
    PREPARING_CAPTURE = "PREPARING_CAPTURE"
    RECORDING = "RECORDING"
    STOPPING = "STOPPING"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class User(Base):
    """Registered Telegram user with permissions."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String(128), nullable=True)
    first_name = Column(String(128), nullable=True)
    last_name = Column(String(128), nullable=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_allowed = Column(Boolean, default=False, nullable=False)
    custom_display_name = Column(String(128), nullable=True)  # User's one-time configured Zoom bot name
    zoom_email = Column(String(256), nullable=True)  # Masked Zoom account email
    zoom_connected_at = Column(DateTime, nullable=True)
    zoom_session_status = Column(String(32), default="DISCONNECTED", nullable=False)  # DISCONNECTED, CONNECTED, EXPIRED
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    jobs = relationship("RecordingJob", back_populates="user", cascade="all, delete-orphan")
    recordings = relationship("Recording", back_populates="user", cascade="all, delete-orphan")


class RecordingJob(Base):
    """Lifecycle tracking for an active or completed recording job."""
    __tablename__ = "recording_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), unique=True, nullable=False, index=True)  # e.g., REC-1001
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    zoom_url = Column(Text, nullable=False)
    meeting_id = Column(String(32), nullable=False, index=True)
    passcode = Column(String(128), nullable=True)
    display_name = Column(String(128), nullable=False)
    
    status = Column(Enum(JobState), default=JobState.QUEUED, nullable=False, index=True)
    status_detail = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=utc_now, nullable=False)
    started_at = Column(DateTime, nullable=True)
    stopped_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="jobs")
    recording = relationship("Recording", back_populates="job", uselist=False, cascade="all, delete-orphan")


class Recording(Base):
    """Metadata for a finalized or uploaded media recording."""
    __tablename__ = "recordings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recording_id = Column(String(64), unique=True, nullable=False, index=True)
    job_id = Column(String(64), ForeignKey("recording_jobs.job_id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    file_path = Column(String(512), nullable=True)  # Local path if preserved
    storage_key = Column(String(512), nullable=False)  # S3 key or relative storage key
    storage_provider = Column(String(32), default="local", nullable=False)
    
    duration_seconds = Column(Float, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    video_format = Column(String(32), default="mp4", nullable=False)
    resolution = Column(String(32), default="1280x720", nullable=True)
    
    upload_status = Column(String(32), default="PENDING", nullable=False)  # PENDING, UPLOADED, FAILED
    completion_status = Column(String(32), default="SUCCESS", nullable=False)  # SUCCESS, PARTIAL, CORRUPTED
    download_url = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=utc_now, nullable=False)

    user = relationship("User", back_populates="recordings")
    job = relationship("RecordingJob", back_populates="recording")


class WorkerHeartbeat(Base):
    """Tracks worker instance health, active jobs, and system metrics."""
    __tablename__ = "worker_heartbeats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    worker_id = Column(String(64), unique=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    active_jobs_count = Column(Integer, default=0, nullable=False)
    last_heartbeat = Column(DateTime, default=utc_now, nullable=False)
    cpu_percent = Column(Float, nullable=True)
    memory_percent = Column(Float, nullable=True)


class AppSetting(Base):
    """Persisted application configuration that is safe to store in DB."""
    __tablename__ = "app_settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=True)
    is_encrypted = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
