"""Standalone Tab Recorder Runner.

Allows recording any browser tab (or Zoom meeting tab) independently from the command line:
Usage:
    python scripts/record_tab.py --url "https://zoom.us/test" --duration 30 --output recordings/tab_sample.mp4
"""

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.browser_manager import BrowserManager
from app.config import get_settings
from app.logging_config import setup_logging
from app.media_processor import MediaProcessor

logger = setup_logging()


async def main():
    parser = argparse.ArgumentParser(description="Standalone Zoom Tab Video Recorder")
    parser.add_argument("--url", "-u", default="https://zoom.us/test", help="URL to navigate to and record")
    parser.add_argument("--duration", "-d", type=int, default=30, help="Recording duration in seconds")
    parser.add_argument("--output", "-o", default=None, help="Output MP4 file path")
    parser.add_argument("--headful", action="store_true", help="Launch visible browser")

    args = parser.parse_args()
    settings = get_settings()

    if args.headful:
        settings.browser_headless = False

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else (settings.local_storage_dir / f"tab_record_{timestamp_str}.mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    temp_video_dir = settings.local_storage_dir / f"temp_rec_{timestamp_str}"
    temp_video_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Starting Tab Recorder...")
    logger.info("Target URL: %s | Duration: %ds | Output: %s", args.url, args.duration, out_path)

    browser_manager = BrowserManager()
    media_processor = MediaProcessor()

    stop_event = asyncio.Event()

    def _sig_handler():
        logger.info("Stop requested. Finalizing recording...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig_handler)
        except NotImplementedError:
            pass

    try:
        # Launch browser with native tab video recording enabled
        page = await browser_manager.launch_browser(record_video_dir=temp_video_dir)
        logger.info("Browser launched. Navigating to tab URL: %s", args.url)
        await page.goto(args.url, timeout=45000, wait_until="domcontentloaded")

        logger.info("🔴 Tab recording in progress for %d seconds (Press Ctrl+C to stop early)...", args.duration)

        elapsed = 0
        step = 1
        while elapsed < args.duration and not stop_event.is_set():
            await asyncio.sleep(step)
            elapsed += step

        logger.info("Stopping tab recording...")
        video_obj = page.video
        await browser_manager.close_browser()

        # Locate recorded webm file
        recorded_file = None
        if video_obj:
            try:
                recorded_file = Path(await video_obj.path())
            except Exception:
                pass

        if not recorded_file or not recorded_file.exists():
            webm_files = list(temp_video_dir.glob("*.webm"))
            if webm_files:
                recorded_file = webm_files[0]

        if not recorded_file or not recorded_file.exists() or recorded_file.stat().st_size == 0:
            raise RuntimeError("Tab recording file was not produced or is empty.")

        logger.info("Raw tab recording saved: %s (%d bytes). Finalizing MP4...", recorded_file.name, recorded_file.stat().st_size)

        final_mp4 = await media_processor.finalize_mp4(recorded_file, out_path)
        probe = await media_processor.probe_file(final_mp4)

        logger.info("✅ Tab Recording Complete!")
        logger.info("Output File: %s", final_mp4)
        logger.info("Duration: %.1fs | Resolution: %s | Size: %.2f MB",
                    probe.get("duration", 0), probe.get("resolution", "N/A"), probe.get("size_bytes", 0) / (1024 * 1024))

    except Exception as exc:
        logger.error("Tab recording failed: %s", exc, exc_info=True)
    finally:
        await browser_manager.close_browser()
        # Clean up temporary raw directory
        import shutil
        shutil.rmtree(temp_video_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
