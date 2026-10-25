"""Automated tests for TabCaptureManager and MediaProcessor."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.capture_manager import TabCaptureManager
from app.media_processor import MediaProcessor


@pytest.mark.asyncio
async def test_tab_capture_manager_lifecycle(tmp_path):
    """Verify start_capture and graceful stop_capture coordination."""
    manager = TabCaptureManager()
    output_file = tmp_path / "test_out.mp4"

    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.is_closing.return_value = False
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        # 1. Start Capture
        res_path = await manager.start_capture(output_file)
        assert manager.is_capturing is True
        assert res_path == output_file

        # Write dummy output to test existence
        output_file.write_bytes(b"mock captured video data")

        # 2. Stop Capture
        stopped_path = await manager.stop_capture()
        assert manager.is_capturing is False
        assert stopped_path == output_file
        mock_proc.stdin.write.assert_called_with(b"q\n")


@pytest.mark.asyncio
async def test_media_processor_probe(tmp_path):
    """Verify MediaProcessor probe extracts stats and validates readable file."""
    processor = MediaProcessor()
    dummy_video = tmp_path / "valid.mp4"
    # Create 5KB dummy file
    dummy_video.write_bytes(b"\x00" * 5120)

    stats = await processor.probe_file(dummy_video)
    assert stats["size_bytes"] == 5120
    assert stats["is_valid"] is True
    assert stats["format"] == "mp4"


@pytest.mark.asyncio
async def test_media_processor_empty_file_fails(tmp_path):
    """Verify MediaProcessor rejects empty or missing files."""
    processor = MediaProcessor()
    empty_video = tmp_path / "empty.mp4"
    empty_video.write_bytes(b"")

    with pytest.raises(Exception):
        await processor.probe_file(empty_video)
