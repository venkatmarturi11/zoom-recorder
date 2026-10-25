# Mobile-First Setup Guide (Android Smartphone Only)

This guide walks you through deploying and configuring the **Telegram-Controlled Zoom Tab Recorder** directly from your Android phone without opening a laptop or desktop computer.

---

## 📱 Prerequisites (All free on Android)

1. **Telegram App** (Installed from Google Play Store)
2. **Google Chrome** (or any mobile web browser on Android)
3. **GitHub Account** (Free account on github.com)
4. **Render Account** (Free tier on render.com)
5. **Cloudflare Account** (Optional for 10GB free external media storage on cloudflare.com)

---

## Step 1: Create your Telegram Bot Token

1. Open **Telegram** on your Android phone.
2. In the search bar at the top, type `@BotFather` and select the verified bot.
3. Tap **Start** (or send `/start`).
4. Send the command:
   ```text
   /newbot
   ```
5. Choose a name for your bot (e.g. `My Cloud Zoom Recorder`).
6. Choose a username ending in `bot` (e.g. `my_zoom_recorder_bot`).
7. **Copy the HTTP API Token** provided by BotFather (looks like `7123456789:ABCdefGhIJKlmNoPQRstuVWXyz123456789`). Keep this safe!

---

## Step 2: Get Your Numerical Telegram User ID

To prevent unauthorized strangers from using your recording bot and exhausting your cloud resources, find your personal numerical ID:

1. In Telegram search, find `@userinfobot`.
2. Tap **Start**.
3. It will reply with your `Id:` (e.g., `123456789`).
4. Note down this numerical ID.

---

## Step 3: Fork or Import the Project to GitHub (Mobile)

1. Open **Chrome** on your Android phone and navigate to `github.com`.
2. Ensure you are signed in.
3. Fork this repository into your account (tap the **Fork** button at the top).

---

## Step 4: Deploy on Render Free Tier via Mobile Browser

1. In Chrome on your phone, go to **[render.com](https://render.com)** and tap **Get Started for Free** (Sign in with your GitHub account).
2. Tap **New +** in the top right menu and choose **Web Service**.
3. Select your forked GitHub repository `zoom-telegram-recorder`.
4. Configure the service:
   - **Name:** `zoom-tab-recorder`
   - **Region:** Choose the region nearest to you (e.g., Oregon, Frankfurt, Singapore).
   - **Environment:** `Docker`
   - **Dockerfile Path:** `./deployment/Dockerfile`
   - **Instance Type:** `Free` (0.5 CPU, 512 MB RAM)
5. Tap **Advanced** -> **Add Environment Variable** and enter:

| Key | Value | Note |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *Your BotFather token* | From Step 1 |
| `ALLOWED_TELEGRAM_USER_IDS` | *Your numerical ID* | From Step 2 |
| `STORAGE_PROVIDER` | `local` (or `s3` for R2) | See Cloud Storage Guide |
| `RECORDING_RESOLUTION` | `1280x720` | Optimizes memory |
| `MAX_CONCURRENT_JOBS` | `1` | Strictly 1 for 512MB RAM |
| `BROWSER_HEADLESS` | `false` | Runs under container Xvfb |

6. Tap **Create Web Service**.
7. Render will build the Docker container and start your bot in ~3 minutes.
8. Copy your Render web service URL (e.g., `https://zoom-tab-recorder.onrender.com`).

---

## Step 5: Prevent Render 15-Minute Sleep via UptimeRobot (Free)

Free Render instances go to sleep after 15 minutes of inactivity. Configure UptimeRobot on your phone so your recorder stays awake 24/7 without shutting down during long meetings:

1. Open your browser on Android and go to **[uptimerobot.com](https://uptimerobot.com)**.
2. Sign in or register for a free account.
3. Tap **+ Add New Monitor**:
   - **Monitor Type:** `HTTP(s)`
   - **Friendly Name:** `Zoom Recorder Bot`
   - **URL (or IP):** `https://<your-app-name>.onrender.com/health` (or `/ping`)
   - **Monitoring Interval:** `Every 5 minutes` (Standard free option)
4. Tap **Create Monitor**.
5. UptimeRobot will now ping your Render app every 5 minutes. Render will recognize the traffic and **never spin down**!

---

## Step 6: One-Time Custom Bot Name Setup

Before recording your first meeting, set the participant name that attendees will see in Zoom:

1. Open **Telegram** on Android and go to your bot.
2. Tap **Start** (or send `/start`).
3. Send the command:
   ```text
   /setname John Doe (Recording Bot)
   ```
   *(Or tap the interactive **'🏷️ Set Bot Name'** button)*
4. The bot will confirm:
   `✅ Zoom Bot Name Saved Successfully!`
5. **You only need to do this once.** The bot permanently remembers this name in its database and will join all your future Zoom meetings with it.

---

## Step 7: Record Your Meetings

1. Send any Zoom meeting link to your bot:
   ```text
   /join https://us02web.zoom.us/j/1234567890?pwd=yourpassword
   ```
2. The bot will join with your custom name, wait for host admission, and begin capturing video + audio.
3. Tap **⏹️ Stop Recording** whenever you wish to finish, or let the meeting conclude.
4. The bot will upload the MP4 and send you a direct download link!
