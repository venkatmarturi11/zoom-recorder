#!/bin/bash
set -e

echo "=== Setting up Zoom Telegram Tab Recorder Environment ==="

# Check Python version
python3 -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'"
echo "Python version check passed."

# Create virtual environment if not present
if [ ! -d "venv" ]; then
    echo "Creating virtual environment in ./venv..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Upgrading pip and installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Installing Playwright Chromium dependencies..."
playwright install-deps chromium 2>/dev/null || true
playwright install chromium

# Create directories
mkdir -p data data/sessions recordings

# Copy .env if missing
if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
fi

echo "=== Setup Completed Successfully! ==="
echo "Next steps:"
echo "1. Edit .env and set your TELEGRAM_BOT_TOKEN and ALLOWED_TELEGRAM_USER_IDS"
echo "2. Run tests: pytest -v"
echo "3. Start the bot: python -m app.main"
