# Free-Tier Cloud Capabilities & Limitations Report

**Target Budget:** ₹0 ($0.00 / Free Tier)

This document provides a realistic, honest evaluation of zero-cost hosting environments for browser tab recording workloads.

---

## 1. Hosting Provider Analysis

### A. Render Free Tier
* **CPU:** 0.5 vCPU (Shared)
* **RAM:** 512 MB
* **Inactivity Spin-down:** Sleeps after 15 minutes of zero inbound HTTP traffic.
* **Persistent Disk:** **None** (Ephemeral local container disk).
* **Bandwidth:** 100 GB / month.

#### Technical Feasibility:
1. **Memory Ceiling:** A single Chromium tab in Zoom Web Client requires **~280MB to 380MB RAM**. FFmpeg capturing video and audio uses **~80MB to 120MB**. Under 512MB RAM, running 1 recording job is technically feasible if:
   - Concurrency is strictly clamped to `MAX_CONCURRENT_JOBS=1`.
   - Resolution is capped at `1280x720` at 30 fps (or 24 fps).
   - Video ultrafast presets are used to minimize frame buffering.
2. **Spin-down Caveat & UptimeRobot Solution:** Render Free web services spin down after 15 minutes without inbound HTTP requests.
   - *Solution:* The application includes a built-in asynchronous HTTP server (`app/http_server.py`) that binds to Render's `$PORT` and exposes `GET /health` and `GET /ping`.
   - *Configuration:* Set up an **UptimeRobot** monitor targeting `https://<your-app-name>.onrender.com/health` with a 5-minute ping interval. Render registers each ping as active HTTP web traffic, resetting the 15-minute countdown and keeping your recorder online 24/7.
3. **Ephemeral Storage:** Local recordings stored on Render Free disk are **wiped when the service restarts or redeploys**.
   - *Requirement:* **Must configure Cloudflare R2 or external S3** (`STORAGE_PROVIDER=s3`) so video files are safely uploaded to permanent cloud storage immediately after capture.

---

### B. Cloudflare R2 (Object Storage)
* **Free Tier Allowance:**
  * 10 GB storage per month ($0).
  * 1,000,000 Class A operations (uploads/puts) per month ($0).
  * 10,000,000 Class B operations (reads/downloads) per month ($0).
  * **$0 Egress fees (Zero bandwidth charges)**.
* **Verdict:** **Recommended zero-cost storage solution.** Storing ~20 to 40 one-hour 720p meetings (approx 200MB each) fits comfortably within the 10GB free tier.

---

### C. Oracle Cloud Always Free Tier (Alternative)
* **Compute:** 4 Ampere ARM cores, 24 GB RAM, 200 GB persistent block storage ($0 forever).
* **Verdict:** The most powerful truly free option, capable of recording multiple 1080p Zoom meetings simultaneously without memory or spin-down constraints. Requires credit card verification for identity during signup, but incurs ₹0 charge.

---

### D. Railway / Fly.io / HuggingFace Spaces
* **Railway:** $5 one-time credit, no permanent free tier.
* **Fly.io:** No longer offers unrestricted free compute without credit card verification.
* **HuggingFace Docker Spaces:** Offers 2 vCPU and 16GB RAM for free with Docker, but public visibility requires careful authentication tokens and port forwarding.

---

## 2. Summary Matrix & Safe Operating Guidelines

| Resource | Free Limit | Recommendation for ₹0 Operation |
|---|---|---|
| **Max Concurrent Recordings** | 1 Job | Never set `MAX_CONCURRENT_JOBS` > 1 on 512MB RAM |
| **Video Resolution** | 1280x720 | Standard 720p 30fps provides optimal audio/video sync |
| **Max Safe Meeting Length** | 2 Hours | Safe cutoff (`MAX_RECORDING_DURATION_SECONDS=7200`) |
| **Storage Retention** | Ephemeral | Upload to Cloudflare R2 to retain videos permanently |
| **Bandwidth** | 100GB/mo | Sufficient for ~400 hours of 720p compressed meetings |
