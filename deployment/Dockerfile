# Production Dockerfile for Zoom Cloud Tab Recorder
FROM ubuntu:22.04

# Prevent interactive prompts during apt installations
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install system dependencies: Python 3, FFmpeg, Xvfb, PulseAudio, DBus
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    ffmpeg \
    xvfb \
    pulseaudio \
    pulseaudio-utils \
    dbus \
    libasound2 \
    libnss3 \
    libatk-bridge2.0-0 \
    libxss1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcups2 \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Alias python to python3
RUN ln -s /usr/bin/python3 /usr/bin/python

WORKDIR /app

# Copy dependency specifications and install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser dependencies and Chromium binary
RUN playwright install-deps chromium \
    && playwright install chromium

# Copy application source code
COPY app/ ./app/
COPY deployment/ ./deployment/
COPY scripts/ ./scripts/

# Ensure entrypoint and healthcheck scripts are executable
RUN chmod +x ./deployment/entrypoint.sh ./scripts/setup.sh ./scripts/healthcheck.py

# Create required persistent storage and session directories
RUN mkdir -p /app/data /app/data/sessions /app/recordings

# Expose webhook port (if enabled)
EXPOSE 8080

# Configure container healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python ./scripts/healthcheck.py || exit 1

ENTRYPOINT ["./deployment/entrypoint.sh"]
