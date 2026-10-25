"""Automated tests for StorageProvider, local file storage, and S3 retry logic."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from botocore.exceptions import ClientError

from app.storage import (
    LocalStorageProvider,
    S3StorageProvider,
    StorageUploadError,
)


@pytest.mark.asyncio
async def test_local_storage_provider(tmp_path):
    """Verify local filesystem storage upload, URL generation, and deletion."""
    provider = LocalStorageProvider(base_dir=tmp_path)
    source_file = tmp_path / "sample.mp4"
    source_file.write_bytes(b"sample video bytes content")

    storage_key = "recordings/sample.mp4"
    saved_path = await provider.upload_file(source_file, storage_key)

    dest_file = tmp_path / storage_key
    assert dest_file.exists()
    assert dest_file.read_bytes() == b"sample video bytes content"

    url = await provider.get_download_url(storage_key)
    assert url.startswith("file://")

    deleted = await provider.delete_file(storage_key)
    assert deleted is True
    assert not dest_file.exists()


@pytest.mark.asyncio
async def test_s3_storage_provider_success(tmp_path):
    """Verify S3 upload and presigned URL generation with mocked boto3 client."""
    with patch("boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_boto.return_value = mock_client
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/zoom/test.mp4?signed=token"

        provider = S3StorageProvider()
        test_file = tmp_path / "test.mp4"
        test_file.write_bytes(b"data")

        key = await provider.upload_file(test_file, "recordings/test.mp4")
        assert key == "recordings/test.mp4"
        mock_client.upload_file.assert_called_once()

        url = await provider.get_download_url("recordings/test.mp4")
        assert "signed=token" in url


@pytest.mark.asyncio
async def test_s3_storage_provider_retries(tmp_path):
    """Verify exponential backoff retries on S3 upload failures."""
    with patch("boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_boto.return_value = mock_client

        # Simulate failures on attempts 1 and 2, then success on attempt 3
        err = ClientError({"Error": {"Code": "500", "Message": "InternalError"}}, "upload_file")
        mock_client.upload_file.side_effect = [err, err, None]

        provider = S3StorageProvider()
        test_file = tmp_path / "retry_test.mp4"
        test_file.write_bytes(b"retry content")

        key = await provider.upload_file(test_file, "recordings/retry.mp4", max_retries=3, initial_backoff=0.01)
        assert key == "recordings/retry.mp4"
        assert mock_client.upload_file.call_count == 3


@pytest.mark.asyncio
async def test_s3_storage_provider_max_retries_exceeded(tmp_path):
    """Verify exception raised when max retries are exceeded."""
    with patch("boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_boto.return_value = mock_client

        err = ClientError({"Error": {"Code": "503", "Message": "SlowDown"}}, "upload_file")
        mock_client.upload_file.side_effect = err

        provider = S3StorageProvider()
        test_file = tmp_path / "fail_test.mp4"
        test_file.write_bytes(b"fail content")

        with pytest.raises(StorageUploadError):
            await provider.upload_file(test_file, "recordings/fail.mp4", max_retries=2, initial_backoff=0.01)

        assert mock_client.upload_file.call_count == 2
