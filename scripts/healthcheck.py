"""Container healthcheck script verifying database, storage, and binary dependencies."""

import asyncio
import os
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.database import get_system_health_stats, init_db


async def check_health() -> bool:
    try:
        settings = get_settings()

        # 1. Check FFmpeg availability
        if not shutil.which(settings.ffmpeg_path):
            print(f"FAIL: FFmpeg binary '{settings.ffmpeg_path}' not found in PATH.")
            return False

        # 2. Check writable directories
        for test_dir in (settings.local_storage_dir, settings.session_storage_dir, Path("./data")):
            test_dir.mkdir(parents=True, exist_ok=True)
            probe_file = test_dir / ".healthcheck_probe"
            probe_file.write_text("health")
            probe_file.unlink()

        # 3. Check Database connectivity & ensure tables exist
        await init_db()
        stats = await get_system_health_stats()
        if not isinstance(stats, dict):
            print("FAIL: Could not query database stats.")
            return False

        print("HEALTHCHECK: OK (DB connected, storage writable, FFmpeg present)")
        return True

    except Exception as exc:
        print(f"FAIL: Healthcheck encountered error: {exc}")
        return False


if __name__ == "__main__":
    healthy = asyncio.run(check_health())
    sys.exit(0 if healthy else 1)
