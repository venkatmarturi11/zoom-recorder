# Troubleshooting & Operations Guide

This guide addresses common situations, errors, and recovery procedures when operating the Zoom Cloud Tab Recorder.

---

## 1. Meeting Joining & Access Issues

### Situation A: "Host did not admit the recording assistant" / Stuck in Waiting Room
* **Symptom:** Bot reports `WAITING_FOR_HOST` and eventually times out.
* **Explanation:** The meeting has a Waiting Room enabled, and the host has not tapped "Admit" for the participant named `Zoom Recorder Bot`.
* **Resolution:** Notify the host in meeting chat or via phone to admit `Zoom Recorder Bot`. The bot waits patiently in the waiting room for up to 10 minutes before timing out.

### Situation B: "Meeting requires registration, sign-in, or host authorization"
* **Symptom:** Status changes to `WAITING_FOR_AUTHORIZATION`.
* **Explanation:** The meeting host configured settings to "Only authenticated users can join" or required a pre-registration form.
* **Resolution:** If pre-registration is required, register the bot's email with the meeting organizer.

### Situation C: "Could not locate a valid 9-to-11 digit Zoom Meeting ID"
* **Symptom:** Bot replies with an Invalid Zoom URL error.
* **Explanation:** The submitted link is missing a valid meeting ID or is not a Zoom URL.
* **Resolution:** Ensure the URL resembles `https://us02web.zoom.us/j/1234567890?pwd=...`.

---

## 2. Cloud & Container Issues

### Situation D: Out of Memory (OOM) Container Crash on Free Tier
* **Symptom:** Job status turns `FAILED` with "Worker restarted during recording execution".
* **Explanation:** On 512MB RAM free instances (e.g., Render Free), running heavy video galleries with multiple participants can exceed 512MB.
* **Resolution:**
  1. Ensure `MAX_CONCURRENT_JOBS=1`.
  2. Set `RECORDING_RESOLUTION=1280x720` (avoid 1080p on free tier).
  3. Ensure `--disable-dev-shm-usage` is enabled in `browser_manager.py` (enabled by default).

### Situation E: Render Free Spin-Down During Meetings
* **Symptom:** Long recording cuts off at ~15 minutes.
* **Explanation:** Free Render web services sleep if they receive no inbound HTTP web requests for 15 minutes.
* **Resolution:** Set up a free recurring monitor (such as [cron-job.org](https://cron-job.org) or [uptimerobot.com](https://uptimerobot.com)) to ping your Render service URL (e.g. `https://your-app.onrender.com/health`) once every 5 minutes.

---

## 3. Audio & Video Playback Issues

### Situation F: Video Plays Smoothly But Has No Audio
* **Symptom:** Captured MP4 is silent.
* **Explanation:** In containerized Linux environments, Chromium requires PulseAudio with a virtual null sink to capture WebRTC audio.
* **Resolution:**
  - Verify container entrypoint (`./deployment/entrypoint.sh`) loaded `module-null-sink`.
  - In Zoom Web Client, the bot automatically clicks "Join Audio by Computer". If the meeting host has muted all participants upon entry, audio will resume as soon as any participant speaks.

### Situation G: Upload to Storage Fails
* **Symptom:** Bot reports `UPLOADING` state failure after multiple attempts.
* **Explanation:** S3 credentials expired or bucket name was incorrect.
* **Resolution:** Verify your `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, and `S3_SECRET_ACCESS_KEY` in environment variables. The worker automatically retries 3 times with exponential backoff.
