"""Pytest shared fixtures, mock objects, and database setup."""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set test environment variables before importing settings
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:ABCdefGhIJKlmNoPQRstuVWXyz123456789"
os.environ["ALLOWED_TELEGRAM_USER_IDS"] = "1001,1002"
os.environ["ADMIN_USER_IDS"] = "1001"
os.environ["STORAGE_PROVIDER"] = "local"
os.environ["CAPTURE_DRIVER"] = "auto"

from app.config import get_settings
from app.database import init_db
from app.models import Base, JobState, RecordingJob, User
import app.database as app_db


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path):
    """Provide a fresh SQLite database for each test run."""
    db_file = tmp_path / "test_recorder.db"
    test_db_url = f"sqlite+aiosqlite:///{db_file}"

    test_engine = create_async_engine(test_db_url, echo=False)
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Monkeypatch the database engine and session factory
    orig_engine = app_db.engine
    orig_factory = app_db.async_session_factory
    app_db.engine = test_engine
    app_db.async_session_factory = test_session_factory

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield test_session_factory

    # Teardown
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()
    app_db.engine = orig_engine
    app_db.async_session_factory = orig_factory


@pytest.fixture
def mock_telegram_update():
    """Build a mock telegram.Update object."""
    def _create(user_id=1001, username="testadmin", chat_id=9999, text="/help", args=None):
        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_user.username = username
        update.effective_user.first_name = "Test"
        update.effective_user.last_name = "User"
        update.effective_user.full_name = "Test User"

        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id

        update.effective_message = AsyncMock()
        update.effective_message.text = text
        update.effective_message.reply_text = AsyncMock()

        update.callback_query = None

        context = MagicMock()
        context.args = args or []
        context.bot = AsyncMock()
        context.bot.send_message = AsyncMock()

        return update, context
    return _create
