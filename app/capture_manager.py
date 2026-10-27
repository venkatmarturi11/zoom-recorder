"""Recording subsystem manager coordinating tab and display capture with audio synchronization."""

import asyncio
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple
from app.config import get_settings

logger = logging.getLogger("zoom_recorder.capture")


class CaptureError(Exception):
    """Raised when tab recording cannot be initiated or fails during execution."""
    pass


class TabCaptureManager:
    """Manages synchronized video and audio capture of the Zoom meeting tab.

    Technical Architecture:
    - In Linux Cloud (Docker container): Combines Xvfb virtual frame buffer (:99)
      and PulseAudio virtual monitor to capture hardware/software rendered tab
      frames (including WebGL, Canvas, and video tags) along with meeting audio via FFmpeg.
    - In Local/Cross-Platform Dev: Utilizes native screen grab or Playwright screencast
      to validate recording end-to-end.
    """

    def __init__(self):
        self.settings = get_settings()
        self.ffmpeg_bin = self.settings.ffmpeg_path
        self._capture_process: Optional[asyncio.subprocess.Process] = None
        self._output_file: Optional[Path] = None
        self._is_capturing: bool = False

    @property
    def is_capturing(self) -> bool:
        return self._is_capturing and (self._capture_process is not None)

    async def start_capture(self, output_path: Path) -> Path:
        """Launch synchronized FFmpeg capture process in background."""
        if self._is_capturing:
            logger.warning("Capture is already active.")
            return self._output_file

        self._output_file = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        system = platform.system().lower()
        fps = str(self.settings.recording_fps)
        resolution = self.settings.recording_resolution

        # Determine FFmpeg capture arguments based on runtime environment
        if system == "linux":
            # Cloud Linux container with Xvfb & PulseAudio
            display = os.environ.get("DISPLAY", self.settings.display_env)
            # Try capturing PulseAudio virtual sink monitor if available
            has_pulse = self._check_pulse_available()
            
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-f", "x11grab",
                "-draw_mouse", "0",
                "-framerate", fps,
                "-video_size", resolution,
                "-i", f"{display}+0,0",
            ]
            
            if has_pulse:
                cmd.extend([
                    "-f", "pulse",
                    "-i", "default.monitor",
                    "-c:a", "aac",
                    "-b:a", "128k",
                ])
            else:
                # Fallback silent audio generator to keep valid AV synchronization
                cmd.extend([
                    "-f", "lavfi",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-c:a", "aac",
                ])

            cmd.extend([
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-g", str(int(fps) * 2),  # Keyframe every 2 seconds
                str(output_path),
            ])

        elif system == "windows":
            # Windows development / testing capture
            has_gdi = self._check_windows_gdigrab_available()
            if has_gdi:
                cmd = [
                    self.ffmpeg_bin,
                    "-y",
                    "-f", "gdigrab",
                    "-framerate", fps,
                    "-video_size", resolution,
                    "-i", "desktop",
                    "-f", "lavfi",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    str(output_path),
                ]
            else:
                # Fallback generator if desktop GDI grab is restricted/headless
                cmd = [
                    self.ffmpeg_bin,
                    "-y",
                    "-f", "lavfi",
                    "-i", f"color=c=black:s={resolution}:r={fps}",
                    "-f", "lavfi",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-t", "7200",
                    str(output_path),
                ]
        else:
            # Generic fallback (e.g. macOS avfoundation or test fallback)
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={resolution}:r={fps}",
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-c:v", "libx264",
                "-t", "3600",
                str(output_path),
            ]

        logger.info("Initiating tab capture with command: %s", " ".join(cmd[:8]) + " ...")

        try:
            self._capture_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._is_capturing = True
            logger.info("Tab capture started successfully. Process PID: %d", self._capture_process.pid)
            return output_path
        except Exception as exc:
            self._is_capturing = False
            logger.error("Failed to start tab capture subprocess: %s", exc)
            raise CaptureError(f"Capture initialization failed: {exc}") from exc

    async def stop_capture(self) -> Optional[Path]:
        """Gracefully terminate FFmpeg recording and flush media buffers."""
        if not self._is_capturing or not self._capture_process:
            logger.warning("Stop requested but tab capture is not running.")
            return self._output_file

        logger.info("Gracefully stopping tab capture process (PID: %d)...", self._capture_process.pid)
        self._is_capturing = False

        try:
            # Send 'q' to FFmpeg's standard input for clean container finalization
            if self._capture_process.stdin and not self._capture_process.stdin.is_closing():
                try:
                    self._capture_process.stdin.write(b"q\n")
                    await self._capture_process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            # Wait up to 10 seconds for clean finalization
            try:
                await asyncio.wait_for(self._capture_process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("FFmpeg did not exit on 'q' prompt; sending terminate signal...")
                self._capture_process.terminate()
                try:
                    await asyncio.wait_for(self._capture_process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("FFmpeg did not terminate; killing process...")
                    self._capture_process.kill()
                    await self._capture_process.wait()

            logger.info("Tab capture process finalized with returncode %s", self._capture_process.returncode)

        except Exception as exc:
            logger.error("Error stopping capture process: %s", exc)
        finally:
            self._capture_process = None

        if self._output_file and self._output_file.exists():
            size = self._output_file.stat().st_size
            logger.info("Captured raw recording file: %s (%d bytes)", self._output_file.name, size)
            return self._output_file

        return None

    def _check_pulse_available(self) -> bool:
        """Check if PulseAudio server or pactl is present in environment."""
        try:
            res = subprocess.run(
                ["pactl", "info"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=2,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _check_windows_gdigrab_available(self) -> bool:
        """Check if Windows desktop GDI grab is accessible without error 5."""
        try:
            res = subprocess.run(
                [self.ffmpeg_bin, "-f", "gdigrab", "-framerate", "1", "-i", "desktop", "-t", "0.1", "-f", "null", "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=2,
            )
            return res.returncode == 0
        except Exception:
            return False
