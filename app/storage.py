"""External storage abstraction supporting Local Filesystem and S3/Cloudflare R2."""

import abc
import asyncio
import logging
from pathlib import Path
from typing import Optional
import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings

logger = logging.getLogger("zoom_recorder.storage")


class StorageUploadError(Exception):
    """Raised when media upload to storage fails after retries."""
    pass


class StorageProvider(abc.ABC):
    """Abstract interface for media storage backends."""

    @abc.abstractmethod
    async def upload_file(self, local_path: Path, storage_key: str) -> str:
        """Upload local file and return the storage identifier or location."""
        pass

    @abc.abstractmethod
    async def get_download_url(self, storage_key: str, expiration_seconds: Optional[int] = None) -> str:
        """Generate an authenticated or direct download URL."""
        pass

    @abc.abstractmethod
    async def delete_file(self, storage_key: str) -> bool:
        """Delete file from storage."""
        pass


class LocalStorageProvider(StorageProvider):
    """Local filesystem storage for development, testing, and staging."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.settings = get_settings()
        self.base_dir = base_dir or self.settings.local_storage_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def upload_file(self, local_path: Path, storage_key: str) -> str:
        target_path = self.base_dir / storage_key
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.resolve() != target_path.resolve():
            target_path.write_bytes(local_path.read_bytes())
        logger.info("Local storage saved: %s", target_path)
        return str(target_path)

    async def get_download_url(self, storage_key: str, expiration_seconds: Optional[int] = None) -> str:
        # In local mode, returns the file path or local file URI
        file_path = self.base_dir / storage_key
        return f"file://{file_path.resolve()}"

    async def delete_file(self, storage_key: str) -> bool:
        file_path = self.base_dir / storage_key
        if file_path.exists():
            file_path.unlink()
            return True
        return False


class S3StorageProvider(StorageProvider):
    """S3-compatible object storage (Cloudflare R2, AWS S3, MinIO, Supabase)."""

    def __init__(self):
        self.settings = get_settings()
        self.bucket = self.settings.s3_bucket_name
        self.url_expiration = self.settings.s3_url_expiration_seconds

        # Configure boto3 client with signature v4 and custom endpoint (for R2)
        client_kwargs = {
            "service_name": "s3",
            "aws_access_key_id": self.settings.s3_access_key_id,
            "aws_secret_access_key": self.settings.s3_secret_access_key,
            "region_name": self.settings.s3_region_name or "auto",
            "config": Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        }
        if self.settings.s3_endpoint_url:
            client_kwargs["endpoint_url"] = self.settings.s3_endpoint_url

        self._s3_client = boto3.client(**client_kwargs)

    async def upload_file(
        self,
        local_path: Path,
        storage_key: str,
        max_retries: int = 3,
        initial_backoff: float = 2.0,
    ) -> str:
        """Upload file with exponential backoff retries."""
        if not local_path.exists():
            raise FileNotFoundError(f"Local file {local_path} does not exist for upload.")

        loop = asyncio.get_running_loop()
        backoff = initial_backoff

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    "Uploading %s to s3://%s/%s (attempt %d/%d)...",
                    local_path.name,
                    self.bucket,
                    storage_key,
                    attempt,
                    max_retries,
                )

                # Execute synchronous boto3 upload in threadpool
                def _do_upload():
                    extra_args = {"ContentType": "video/mp4"}
                    self._s3_client.upload_file(
                        Filename=str(local_path),
                        Bucket=self.bucket,
                        Key=storage_key,
                        ExtraArgs=extra_args,
                    )

                await loop.run_in_executor(None, _do_upload)
                logger.info("Successfully uploaded %s to S3 storage_key: %s", local_path.name, storage_key)
                return storage_key

            except (BotoCoreError, ClientError) as exc:
                logger.warning(
                    "S3 upload attempt %d failed: %s. Retrying in %.1fs...",
                    attempt,
                    exc,
                    backoff,
                )
                if attempt == max_retries:
                    raise StorageUploadError(f"Failed to upload to S3 after {max_retries} attempts: {exc}") from exc
                await asyncio.sleep(backoff)
                backoff *= 2.0

        raise StorageUploadError("Exceeded max upload retries.")

    async def get_download_url(self, storage_key: str, expiration_seconds: Optional[int] = None) -> str:
        """Generate presigned download URL."""
        loop = asyncio.get_running_loop()
        expires_in = expiration_seconds or self.url_expiration

        def _generate_presigned():
            return self._s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": storage_key,
                },
                ExpiresIn=expires_in,
            )

        return await loop.run_in_executor(None, _generate_presigned)

    async def delete_file(self, storage_key: str) -> bool:
        loop = asyncio.get_running_loop()

        def _do_delete():
            self._s3_client.delete_object(Bucket=self.bucket, Key=storage_key)

        try:
            await loop.run_in_executor(None, _do_delete)
            return True
        except Exception as exc:
            logger.error("Failed to delete S3 object %s: %s", storage_key, exc)
            return False


def get_storage_provider() -> StorageProvider:
    """Factory to retrieve configured StorageProvider."""
    settings = get_settings()
    if settings.storage_provider == "s3" and settings.s3_access_key_id:
        return S3StorageProvider()
    return LocalStorageProvider()
