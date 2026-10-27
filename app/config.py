"""Application configuration loaded from environment variables and .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Any, List, Literal, Optional, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration settings for the Zoom Tab Recorder."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram Bot Configuration
    telegram_bot_token: str = Field(
        default="",
        description="Telegram bot token obtained from @BotFather",
    )
    allowed_telegram_user_ids: Any = Field(
        default_factory=list,
        description="List of authorized Telegram user IDs permitted to interact with the bot",
    )
    admin_user_ids: Any = Field(
        default_factory=list,
        description="List of admin Telegram user IDs permitted to execute administrative commands",
    )

    # Database Configuration
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/recorder.db",
        description="Async SQLAlchemy database connection URI",
    )

    # Storage Configuration
    storage_provider: Literal["local", "s3"] = Field(
        default="local",
        description="External storage provider: 'local' (development/testing) or 's3' (AWS S3, Cloudflare R2, MinIO)",
    )
    local_storage_dir: Path = Field(
        default=Path("./recordings"),
        description="Directory for local recording storage and temporary staging",
    )
    s3_endpoint_url: Optional[str] = Field(
        default=None,
        description="S3 endpoint URL (required for Cloudflare R2, MinIO, or custom S3-compatible providers)",
    )
    s3_bucket_name: str = Field(
        default="zoom-recordings",
        description="Bucket name for recording storage",
    )
    s3_access_key_id: str = Field(
        default="",
        description="S3 access key ID",
    )
    s3_secret_access_key: str = Field(
        default="",
        description="S3 secret access key",
    )
    s3_region_name: str = Field(
        default="auto",
        description="S3 region name (default 'auto' for Cloudflare R2 or 'us-east-1' for AWS)",
    )
    s3_url_expiration_seconds: int = Field(
        default=86400,  # 24 hours
        description="Expiration time in seconds for presigned authenticated download URLs",
    )
    cleanup_local_files_after_upload: bool = Field(
        default=True,
        description="Whether to remove the local video file after successful external upload",
    )

    # Recording & Media Settings
    bot_display_name: str = Field(
        default="Zoom Recorder Bot (Authorized Assistant)",
        description="Identifiable participant name shown in Zoom meetings",
    )
    max_recording_duration_seconds: int = Field(
        default=7200,  # 2 hours
        description="Safety timeout to prevent unbounded cloud recordings",
    )
    recording_fps: int = Field(
        default=30,
        description="Frames per second for captured video stream",
    )
    recording_resolution: str = Field(
        default="1280x720",
        description="Resolution for virtual display and video recording (WIDTHxHEIGHT)",
    )
    ffmpeg_path: str = Field(
        default="ffmpeg",
        description="Path or binary name for FFmpeg executable",
    )

    # Browser & Capture Environment
    browser_headless: bool = Field(
        default=True,
        description="Whether to run Chromium in headless mode (False if running under Xvfb in container)",
    )
    display_env: str = Field(
        default=":99",
        description="X11 DISPLAY variable when running in container with Xvfb",
    )
    capture_driver: Literal["auto", "xvfb", "screencast"] = Field(
        default="auto",
        description="Capture subsystem driver: 'xvfb' (Linux container display+audio capture) or 'screencast' (cross-platform)",
    )
    max_concurrent_jobs: int = Field(
        default=1,
        description="Maximum concurrent recordings allowed (default 1 to respect free-tier RAM limits)",
    )

    # Security & Encryption
    app_secret_key: str = Field(
        default="",
        description="Base64 Fernet encryption key used to encrypt saved Zoom cookies/session credentials",
    )
    session_storage_dir: Path = Field(
        default=Path("./data/sessions"),
        description="Directory for securely stored encrypted browser session state",
    )

    # Webhook Mode (Optional for Production Serverless/Cloud)
    webhook_url: Optional[str] = Field(
        default=None,
        description="Public URL for Telegram Webhook mode (if None, polling is used)",
    )
    webhook_port: int = Field(
        default=8080,
        description="Port for incoming Telegram webhook and UptimeRobot HTTP server",
    )

    public_app_url: Optional[str] = Field(
        default=None,
        description="Public HTTPS URL for authentication portal and webhooks (e.g. https://my-app.onrender.com)",
    )

    # Official Zoom OAuth 2.0 Authentication
    zoom_client_id: Optional[str] = Field(
        default=None,
        description="Zoom App Marketplace OAuth Client ID",
    )
    zoom_client_secret: Optional[str] = Field(
        default=None,
        description="Zoom App Marketplace OAuth Client Secret",
    )
    zoom_oauth_scope: str = Field(
        default="user:read",
        description="Minimal OAuth scope genuinely required for user identity authorization",
    )

    @property
    def effective_port(self) -> int:
        """Port to bind for HTTP keep-alive and webhooks (respects Render's $PORT environment variable)."""
        import os
        env_port = os.environ.get("PORT")
        if env_port and env_port.isdigit():
            return int(env_port)
        return self.webhook_port

    @property
    def effective_public_url(self) -> str:
        """Determines the public base URL for the mobile authentication portal."""
        import os
        if self.public_app_url:
            return self.public_app_url.rstrip("/")
        render_ext_url = os.environ.get("RENDER_EXTERNAL_URL")
        if render_ext_url:
            return render_ext_url.rstrip("/")
        render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        if render_host:
            return f"https://{render_host}"
        if self.webhook_url:
            return self.webhook_url.rstrip("/")
        return f"http://localhost:{self.effective_port}"

    @property
    def zoom_oauth_redirect_uri(self) -> str:
        """Official Zoom OAuth 2.0 callback URL registered in the Zoom App Marketplace."""
        return f"{self.effective_public_url}/oauth/callback"

    @field_validator("allowed_telegram_user_ids", "admin_user_ids", mode="after")
    @classmethod
    def parse_user_ids(cls, v) -> List[int]:
        """Parse integers, comma-separated strings, or iterables into a List[int]."""
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            return [int(uid.strip()) for uid in v.split(",") if uid.strip().isdigit()]
        if isinstance(v, (list, tuple, set)):
            return [int(x) for x in v]
        return []

    @property
    def resolution_tuple(self) -> tuple[int, int]:
        """Return (width, height) integers from recording_resolution."""
        parts = self.recording_resolution.lower().split("x")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
        return 1280, 720


@lru_cache()
def get_settings() -> Settings:
    """Retrieve cached application settings instance."""
    settings = Settings()
    # Ensure local directory paths exist
    settings.local_storage_dir.mkdir(parents=True, exist_ok=True)
    settings.session_storage_dir.mkdir(parents=True, exist_ok=True)
    Path("./data").mkdir(parents=True, exist_ok=True)
    return settings
