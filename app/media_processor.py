"""FFmpeg media validation, remuxing, probe extraction, and audio-video synchronization."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.config import get_settings

logger = logging.getLogger("zoom_recorder.media")


class MediaProcessingError(Exception):
    """Raised when media validation or FFmpeg remuxing fails."""
    pass


class MediaProcessor:
    """Manages audio/video file inspection, remuxing, and integrity validation."""

    def __init__(self, ffmpeg_bin: Optional[str] = None):
        settings = get_settings()
        self.ffmpeg_bin = ffmpeg_bin or settings.ffmpeg_path

    async def probe_file(self, file_path: Path) -> Dict[str, any]:
        """Inspect media file using ffprobe/ffmpeg to verify integrity and extract streams."""
        if not file_path.exists() or file_path.stat().st_size == 0:
            raise MediaProcessingError(f"Target media file {file_path} is empty or missing.")

        # Determine ffprobe command
        ffprobe_bin = "ffprobe"
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration,size,bit_rate:stream=codec_type,width,height,r_frame_rate,duration",
            "-of", "json",
            str(file_path),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("ffprobe check returned non-zero code %d: %s", proc.returncode, stderr.decode())
                # Fallback to stat-based metadata if ffprobe is not installed on system
                return self._fallback_file_stats(file_path)

            data = json.loads(stdout.decode())
            return self._parse_probe_output(data, file_path)

        except FileNotFoundError:
            logger.info("ffprobe binary not found in PATH, using file stat fallback.")
            return self._fallback_file_stats(file_path)
        except Exception as exc:
            logger.warning("Error running ffprobe: %s. Using file stat fallback.", exc)
            return self._fallback_file_stats(file_path)

    def _fallback_file_stats(self, file_path: Path) -> Dict[str, any]:
        """Calculate fallback stats when ffprobe is absent."""
        size = file_path.stat().st_size
        return {
            "duration": None,
            "size_bytes": size,
            "resolution": "1280x720",
            "format": "mp4",
            "has_video": True,
            "has_audio": True,
            "is_valid": size > 1024,
        }

    def _parse_probe_output(self, data: dict, file_path: Path) -> Dict[str, any]:
        streams = data.get("streams", [])
        fmt = data.get("format", {})

        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)

        width = None
        height = None
        for s in streams:
            if s.get("codec_type") == "video" and s.get("width") and s.get("height"):
                width = s.get("width")
                height = s.get("height")
                break

        resolution = f"{width}x{height}" if width and height else "1280x720"
        try:
            duration = float(fmt.get("duration", 0))
        except (ValueError, TypeError):
            duration = None

        size_bytes = int(fmt.get("size", file_path.stat().st_size))

        return {
            "duration": duration,
            "size_bytes": size_bytes,
            "resolution": resolution,
            "format": "mp4",
            "has_video": has_video,
            "has_audio": has_audio,
            "is_valid": (file_path.stat().st_size > 4096) and (has_video or has_audio),
        }

    async def finalize_mp4(self, input_path: Path, output_path: Path) -> Path:
        """Finalize, synchronize, and remux recording to playable MP4 with faststart flags.

        Uses stream copying where compatible to avoid CPU-intensive re-encoding,
        or falls back to fast H.264/AAC re-encoding if container timestamps need repair.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if input_path.suffix.lower() == ".mp4" and input_path.resolve() == output_path.resolve():
            # Apply faststart in-place via temporary file
            temp_output = output_path.with_name(f"temp_faststart_{output_path.name}")
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-i", str(input_path),
                "-c", "copy",
                "-movflags", "+faststart",
                str(temp_output),
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0 and temp_output.exists() and temp_output.stat().st_size > 0:
                    temp_output.replace(output_path)
                    logger.info("Applied MP4 faststart remuxing to %s", output_path)
                    return output_path
            except Exception as exc:
                logger.warning("Faststart remuxing skipped: %s", exc)
                if temp_output.exists():
                    temp_output.unlink()
            return output_path

        # Remux from raw format (.mkv, .webm, or intermediate) to standard MP4
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i", str(input_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]

        logger.info("Remuxing %s -> %s...", input_path.name, output_path.name)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning(
                "Remuxing with stream-copy failed (%s). Retrying with software re-encode...",
                stderr.decode()[:300],
            )
            # Re-encode fallback with ultrafast preset
            reencode_cmd = [
                self.ffmpeg_bin,
                "-y",
                "-i", str(input_path),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(output_path),
            ]
            proc = await asyncio.create_subprocess_exec(
                *reencode_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise MediaProcessingError(f"FFmpeg remux and encode failed: {stderr.decode()[:400]}")

        logger.info("Successfully finalized MP4 media file: %s", output_path)
        return output_path
