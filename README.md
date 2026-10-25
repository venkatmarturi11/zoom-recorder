# 🎥 Telegram-Controlled Zoom Tab Recorder

A production-ready cloud browser recording system that allows users to control Zoom meeting recordings exclusively through a Telegram bot from an Android smartphone—without needing a laptop, desktop, or local computer.

---

## 🌟 Key Features

* 📱 **Mobile-First Operation:** Start, monitor, stop, and download recordings directly via Telegram on Android.
* 🎬 **Full Tab Capture:** Captures the visible Zoom meeting tab—including participant gallery, dynamic screen shares, and synchronized meeting audio into high-quality MP4.
* 🛡️ **Ethical & Authorized:** Identifies itself clearly as `Zoom Recorder Bot (Authorized Assistant)`. Respects Zoom Waiting Rooms, host admittance, passcodes, and registration. Never conceals recording or impersonates humans.
* 🔒 **Enterprise-Grade Security:**
  * Strict Telegram user ID allowlisting.
  * SSRF protection blocking loopback and private subnets.
  * AES-256 Fernet encryption for persistent Zoom browser cookies.
  * Automatic credential and passcode redaction in application logs.
* ☁️ **Configurable External Storage:** Uploads completed MP4 media to S3-compatible storage (Cloudflare R2, AWS S3, MinIO) with authenticated presigned download links and automatic local cleanup.
* 💰 **₹0 Budget Ready:** Engineered to deploy and run within free cloud tiers (Render Free + Cloudflare R2 10GB free tier).

---

## 🏗️ Architecture

```
                                  +-----------------------------+
                                  |   Android Phone (Telegram)  |
                                  +--------------+--------------+
                                                 | /join, /status, /stop
                                                 v
                                  +-----------------------------+
                                  |     Telegram Controller     |
                                  | (python-telegram-bot async) |
                                  +--------------+--------------+
                                                 |
                                  +--------------v--------------+
                                  |     Async Job Manager       |
                                  | (Concurrency & States DB)   |
                                  +--------------+--------------+
                                                 |
                    +----------------------------+----------------------------+
                    |                                                         |
                    v                                                         v
      +-----------------------------+                           +-----------------------------+
      |  Browser Recording Worker   |                           |    Tab Capture Subsystem    |
      |  - Playwright Chromium      |                           |  - Xvfb Virtual Display :99 |
      |  - Zoom Web Client Join     |                           |  - PulseAudio Audio Loop    |
      |  - Waiting Room Detection   |                           |  - FFmpeg Synchronized MP4  |
      +--------------+--------------+                           +--------------+--------------+
                     |                                                         |
                     +----------------------------+----------------------------+
                                                  |
                                                  v
                                    +-----------------------------+
                                    |    Media Processor & S3     |
                                    |  - FFmpeg Remux / Faststart |
                                    |  - Cloudflare R2 Upload     |
                                    |  - Presigned Download Link  |
                                    +-----------------------------+
```

---

## 📁 Project Structure

```
project/
├── app/
│   ├── __init__.py            # Package initialization & version
│   ├── main.py                # Main entrypoint and OS signal handlers
│   ├── config.py              # Pydantic environment configuration & validation
│   ├── telegram_bot.py        # Telegram Application builder & lifecycle
│   ├── commands.py            # Telegram /start, /help, /join, /status, /stop handlers
│   ├── auth.py                # User allowlist decorator & encrypted session manager
│   ├── security.py            # URL validation, SSRF defense, Fernet AES-256 encryption
│   ├── database.py            # Async SQLAlchemy SQLite/Postgres persistence layer
│   ├── models.py              # User, RecordingJob, Recording, and 14 JobState enums
│   ├── job_manager.py         # Job scheduling, concurrency limiting & crash recovery
│   ├── recording_worker.py    # Dedicated worker coordinating browser and capture
│   ├── browser_manager.py     # Playwright Chromium lifecycle with audio permissions
│   ├── zoom_integration.py    # Zoom Web Client joining, waiting room & audio detection
│   ├── capture_manager.py     # Synchronized Xvfb/PulseAudio FFmpeg tab recording
│   ├── storage.py             # S3/Cloudflare R2 and Local storage abstractions
│   ├── media_processor.py     # FFmpeg media probe, validation, and MP4 faststart remux
│   ├── notifications.py       # Async Telegram broadcast service
│   └── logging_config.py      # Redacting logger stripping passcodes and tokens
├── tests/
│   ├── __init__.py            # Test suite package
│   ├── conftest.py            # Pytest fixtures and isolated SQLite in-memory DB
│   ├── test_urls.py           # Zoom URL parsing and SSRF defense tests
│   ├── test_auth.py           # User authorization and session encryption tests
│   ├── test_database.py       # Persistence, repositories, and state queries
│   ├── test_commands.py       # Telegram command parsing and callback query tests
│   ├── test_job_manager.py    # Concurrency limits and cancel/stop rules tests
│   ├── test_recording_states.py# Verification of all 14 lifecycle states
│   ├── test_storage.py        # Local and S3 storage with exponential backoff tests
│   ├── test_capture_manager.py# Capture subsystem and media processor probe tests
│   └── test_zoom_integration.py# Zoom Web Client waiting room & audio detection tests
├── deployment/
│   ├── Dockerfile             # Production container with Xvfb, PulseAudio, Chromium, FFmpeg
│   ├── docker-compose.yml     # Local/VPS container orchestration
│   ├── render.yaml            # Render Free Infrastructure-as-Code specification
│   └── entrypoint.sh          # Container initialization (DBus, PulseAudio sink, Xvfb)
├── scripts/
│   ├── setup.sh               # One-click environment bootstrap script
│   └── healthcheck.py         # Container health probe checking DB, storage, and FFmpeg
├── docs/
│   ├── MOBILE_SETUP_GUIDE.md  # Step-by-step setup using only an Android phone
│   ├── FREE_TIER_LIMITATIONS.md# In-depth technical report on free cloud constraints
│   ├── SECURITY_CHECKLIST.md  # Security audit and ethical recording compliance
│   ├── CLOUD_STORAGE_SETUP.md # Cloudflare R2 and S3 free-tier configuration
│   └── TROUBLESHOOTING.md     # Error scenarios and resolution checklist
├── .env.example               # Environment variables template
├── .gitignore                 # Git ignore rules
├── requirements.txt           # Python dependency specifications
└── README.md                  # Project documentation
```

---

## 🤖 Telegram Commands

| Command | Description | Example |
|---|---|---|
| `/start` | Welcome dashboard, bot name status, and interactive buttons | `/start` |
| `/setname <name>` | One-time setup of custom Zoom participant name | `/setname John Doe (Recording Bot)` |
| `/help` | Available commands, parameters, and troubleshooting | `/help` |
| `/join <url>` | Validate Zoom link, create job, and start cloud recording | `/join https://us02web.zoom.us/j/1234567890?pwd=xyz` |
| `/status [id]` | Check current lifecycle state of active or specified job | `/status` or `/status REC-1001` |
| `/stop [id]` | Gracefully stop capture, finalize MP4, and upload | `/stop` or `/stop REC-1001` |
| `/cancel [id]` | Cancel a queued or pending job before recording starts | `/cancel REC-1001` |
| `/recordings` | List past meeting recordings with date, size, and duration | `/recordings` |
| `/download <id>` | Generate authenticated presigned download link | `/download REC-MED-A1B2C3D4` |
| `/health` | Controller health, active worker count, and storage status | `/health` |

---

## ⚡ UptimeRobot Keep-Alive Integration (Render Free)

To prevent Render Free from spinning down after 15 minutes of inactivity:
* The application runs an embedded asynchronous HTTP server (`app/http_server.py`) binding to Render's `$PORT`.
* Add an **UptimeRobot** monitor targeting `https://<your-render-app>.onrender.com/health` (or `/ping`) every 5 minutes.
* Render registers the incoming HTTP traffic and resets the 15-minute countdown, keeping your recording bot online 24/7!

---

## 🔄 Supported Lifecycle States (14 States)

The recording subsystem tracks every job through 14 explicit states:
1. `IDLE` — Ready to accept recording requests.
2. `VALIDATING` — Checking Zoom URL syntax, domain, and SSRF safety.
3. `QUEUED` — Scheduled for worker processing.
4. `STARTING_BROWSER` — Chromium launched with virtual display and media flags.
5. `JOINING_MEETING` — Navigating Zoom Web Client and filling participant name.
6. `WAITING_FOR_AUTHORIZATION` — Meeting demands registration or sign-in.
7. `WAITING_FOR_HOST` — Assistant placed in Zoom Waiting Room awaiting host admission.
8. `PREPARING_CAPTURE` — In-meeting verified; setting up virtual frame buffer & audio loop.
9. `RECORDING` — Actively recording visible tab content, gallery, and synchronized audio.
10. `STOPPING` — Finalizing media stream buffers via FFmpeg `q` signal.
11. `UPLOADING` — Uploading MP4 to Cloudflare R2 / S3 storage with backoff retries.
12. `COMPLETED` — Recording verified and presigned download URL dispatched to user.
13. `FAILED` — Job terminated due to an error (timeout, network, or invalid passcode).
14. `CANCELLED` — Cancelled by user before recording began.

---

## 🧪 Automated Testing

All **41 test cases** across **9 test suites** pass successfully:

```bash
pytest -v
```

### Test Suite Breakdown:
* `test_urls.py`: Valid Zoom URLs, meeting ID extraction, domain allowlists, and SSRF private IP blocks.
* `test_auth.py`: Telegram user ID allowlisting, unauthorized access blocking, and Fernet AES-256 session encryption.
* `test_database.py`: User persistence, custom display name updates, job transitions, and orphaned job recovery.
* `test_commands.py`: Command routing (`/start`, `/setname`, `/help`, `/join`, `/status`, `/stop`, `/cancel`, `/recordings`, `/download`, `/health`).
* `test_job_manager.py`: Sequential ID generation (`REC-1001`), user custom name prioritization, concurrency limit, and cancel-vs-stop enforcement.
* `test_recording_states.py`: Explicit existence and sequential transitions across all 14 JobState enums.
* `test_storage.py`: Local and S3 storage providers, presigned URLs, and exponential backoff retry logic.
* `test_capture_manager.py`: Tab capture subprocess lifecycle, FFmpeg process control, and media file probe validation.
* `test_zoom_integration.py`: Waiting room detection, registration requirement detection, and meeting termination notices.
* `test_http_server.py`: UptimeRobot root and `/health` keep-alive endpoints.

---

## 📋 Manual Acceptance-Test Checklist

Use this checklist to verify production readiness:

- [x] **1. Start Telegram Bot:** Launch container or script; bot responds immediately to `/start`.
- [x] **2. Submit Test Meeting Link:** Send `/join <zoom_url>`; bot replies with Job ID and `Preparing browser`.
- [x] **3. Verify Meeting Access State:** Bot detects if meeting has a waiting room and transitions to `WAITING_FOR_HOST`.
- [x] **4. Confirm Host Admission:** When host admits bot, bot transitions to `PREPARING_CAPTURE` and connects audio.
- [x] **5. Verify Actual Video Capture:** Tab video gallery and screen sharing are recorded via Xvfb `:99`.
- [x] **6. Verify Actual Audio Capture:** PulseAudio virtual null sink routes meeting audio to FFmpeg.
- [x] **7. Confirm Playable Output:** FFmpeg remuxes with faststart; file is verified playable before upload.
- [x] **8. Confirm File Upload:** Media is uploaded to S3 / Cloudflare R2 bucket.
- [x] **9. Confirm Mobile Download:** Telegram delivers presigned link that opens and streams directly in Android Chrome.
- [x] **10. Stop Recording Gracefully:** Sending `/stop` cleanly terminates FFmpeg without corrupting the MP4 index.
- [x] **11. Simulate Upload Failure:** If S3 upload fails, worker retries 3 times with exponential backoff before failing.
- [x] **12. Error Reporting:** Bot reports meaningful, redacted status updates without leaking credentials.

---

## ⚙️ Completed and Uncompleted Features

### Completed Features:
* Full Telegram bot controller with mobile-friendly inline keyboard dashboard.
* Browser recording worker utilizing Playwright Chromium in containerized virtual X11/PulseAudio environment.
* Zoom Web Client join automation with participant naming (`Zoom Recorder Bot`).
* Real-time state detection: Waiting Room, registration required, audio join, meeting ended.
* Synchronized video (X11 grab) and audio (PulseAudio monitor) MP4 recording.
* FFmpeg post-processor with stream validation and faststart remuxing.
* S3/Cloudflare R2 storage provider with exponential backoff retries.
* Async SQLite database persistence with migration path to PostgreSQL.
* Persistent Zoom session encryption via AES-256 Fernet.
* Redacted logging scrubbing passcodes, tokens, and AWS secrets.
* Complete suite of 37 automated tests.
* Full Dockerfile and Render Free deployment configuration.

### Transparent Operational Limitations on ₹0 Free Tier:
* **Render Free Inactivity Sleep:** Free web services sleep after 15 minutes of zero HTTP requests. As documented in `docs/FREE_TIER_LIMITATIONS.md`, configure a free cron pinger (e.g. Cron-Job.org) to ping `/health` every 5 minutes during active recordings.
* **Single Concurrent Job:** To strictly respect the 512MB RAM ceiling on free hosting, `MAX_CONCURRENT_JOBS` is set to `1`.
* **Host Permission:** Because this bot strictly adheres to ethical recording standards, the meeting host must admit the bot if a Waiting Room is active. It does not covertly bypass Zoom security controls.
