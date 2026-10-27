"""HTTP server providing keep-alive health probes and the secure mobile Zoom authentication portal."""

import json
import logging
from aiohttp import web

from app.config import get_settings
from app.database import get_system_health_stats
from app.login_session import get_login_session_manager
from app.zoom_oauth import ZoomOAuthError, get_zoom_oauth_service

logger = logging.getLogger("zoom_recorder.http")

LOGIN_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Connect Zoom Account &bull; Cloud Recorder</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(22, 30, 49, 0.85);
      --card-border: rgba(255, 255, 255, 0.1);
      --accent: #2563eb;
      --accent-hover: #1d4ed8;
      --accent-gradient: linear-gradient(135deg, #2563eb 0%, #3b82f6 50%, #60a5fa 100%);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --success: #10b981;
      --danger: #ef4444;
      --warning: #f59e0b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      background-image: 
        radial-gradient(at 0% 0%, rgba(37, 99, 235, 0.25) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(96, 165, 250, 0.15) 0px, transparent 50%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .container {
      width: 100%;
      max-width: 440px;
      background: var(--card-bg);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--card-border);
      border-radius: 24px;
      padding: 32px 24px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
      transition: all 0.3s ease;
    }
    .header {
      text-align: center;
      margin-bottom: 24px;
    }
    .logo-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 56px;
      height: 56px;
      border-radius: 16px;
      background: var(--accent-gradient);
      box-shadow: 0 8px 16px -4px rgba(37, 99, 235, 0.5);
      margin-bottom: 16px;
    }
    .logo-badge svg { width: 30px; height: 30px; fill: white; }
    h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.02em; margin-bottom: 6px; }
    p.subtitle { font-size: 14px; color: var(--text-muted); line-height: 1.5; }
    .security-notice {
      background: rgba(37, 99, 235, 0.08);
      border: 1px solid rgba(37, 99, 235, 0.25);
      border-radius: 12px;
      padding: 12px 14px;
      margin-bottom: 20px;
      font-size: 13px;
      color: #93c5fd;
      display: flex;
      align-items: flex-start;
      gap: 10px;
    }
    .security-notice svg { flex-shrink: 0; width: 18px; height: 18px; fill: #60a5fa; margin-top: 1px; }
    .form-group { margin-bottom: 16px; }
    label { display: block; font-size: 13px; font-weight: 600; color: #cbd5e1; margin-bottom: 6px; }
    input[type="email"], input[type="password"], input[type="text"], textarea {
      width: 100%;
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 12px;
      padding: 14px 16px;
      font-size: 15px;
      color: white;
      outline: none;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    input:focus, textarea:focus {
      border-color: #3b82f6;
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.3);
    }
    .btn {
      width: 100%;
      padding: 15px;
      background: var(--accent-gradient);
      border: none;
      border-radius: 12px;
      color: white;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4);
      transition: transform 0.15s, opacity 0.15s, box-shadow 0.15s;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }
    .btn:active { transform: scale(0.98); }
    .btn:disabled { opacity: 0.6; cursor: not-allowed; }
    .status-box {
      display: none;
      padding: 16px;
      border-radius: 12px;
      margin-top: 16px;
      font-size: 14px;
      line-height: 1.5;
      text-align: center;
    }
    .status-authenticating {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
      background: rgba(37, 99, 235, 0.1);
      border: 1px solid rgba(37, 99, 235, 0.3);
      color: #93c5fd;
    }
    .status-waiting-mfa {
      display: block;
      background: rgba(245, 158, 11, 0.1);
      border: 1px solid rgba(245, 158, 11, 0.3);
      color: #fde68a;
    }
    .status-success {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 10px;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #6ee7b7;
    }
    .status-failed {
      display: block;
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #fca5a5;
    }
    .spinner {
      width: 28px;
      height: 28px;
      border: 3px solid rgba(255, 255, 255, 0.2);
      border-top-color: white;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .tab-toggle {
      display: flex;
      gap: 8px;
      margin-bottom: 20px;
      background: rgba(15, 23, 42, 0.5);
      padding: 4px;
      border-radius: 12px;
    }
    .tab-btn {
      flex: 1;
      padding: 10px;
      background: transparent;
      border: none;
      border-radius: 8px;
      color: var(--text-muted);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }
    .tab-btn.active {
      background: rgba(255, 255, 255, 0.1);
      color: white;
    }
    .check-icon {
      width: 48px;
      height: 48px;
      fill: var(--success);
      margin-bottom: 4px;
    }
    .footer-text {
      margin-top: 24px;
      font-size: 12px;
      color: #64748b;
      text-align: center;
    }
  </style>
</head>
<body>
  <div class="container" id="mainContainer">
    <div class="header">
      <div class="logo-badge">
        <svg viewBox="0 0 24 24"><path d="M18 10.48V6c0-1.1-.9-2-2-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2v-4.48l4 3.98v-11l-4 3.98zM16 18H4V6h12v12z"/></svg>
      </div>
      <h1>Connect Zoom Account</h1>
      <p class="subtitle">Authenticate once in the cloud browser to record meetings as an authorized participant.</p>
    </div>

    <div class="security-notice">
      <svg viewBox="0 0 24 24"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z"/></svg>
      <div>
        <strong>Zero Password Storage:</strong> Credentials are sent securely over HTTPS to the isolated cloud Chromium instance. Your session is encrypted with AES-256 and never logged or exposed.
      </div>
    </div>

    <div class="tab-toggle">
      <button class="tab-btn active" id="tabAuto" onclick="switchTab('auto')">Cloud Browser Login</button>
      <button class="tab-btn" id="tabImport" onclick="switchTab('import')">Import Session JSON</button>
    </div>

    <!-- Mode 1: Cloud Browser Direct Login -->
    <div id="sectionAuto">
      <form id="loginForm" onsubmit="handleCloudLogin(event)">
        <div class="form-group">
          <label for="email">Zoom Email Address</label>
          <input type="email" id="email" required autocomplete="username" placeholder="you@company.com">
        </div>
        <div class="form-group">
          <label for="password">Zoom Password</label>
          <input type="password" id="password" required autocomplete="current-password" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;">
        </div>
        <button type="submit" class="btn" id="submitBtn">
          <span>Sign In in Cloud Browser</span>
        </button>
      </form>
    </div>

    <!-- Mode 2: Direct Session JSON Import -->
    <div id="sectionImport" style="display: none;">
      <form id="importForm" onsubmit="handleSessionImport(event)">
        <div class="form-group">
          <label for="sessionJson">Paste Playwright/Puppeteer storage_state.json</label>
          <textarea id="sessionJson" rows="6" required placeholder='{"cookies": [{"name": "_zm_ssid", ...}]}'></textarea>
        </div>
        <button type="submit" class="btn" id="importBtn">
          <span>Verify & Encrypt Session</span>
        </button>
      </form>
    </div>

    <!-- Dynamic Status Displays -->
    <div id="statusAuthenticating" class="status-box status-authenticating">
      <div class="spinner"></div>
      <div>
        <strong>Authenticating in Cloud Browser...</strong>
        <p style="font-size: 13px; color: #94a3b8; margin-top: 4px;">Opening Zoom login portal and navigating security steps. Please wait...</p>
      </div>
    </div>

    <div id="statusMfa" class="status-box status-waiting-mfa">
      <h3 style="margin-bottom: 8px;">Two-Factor Verification Required</h3>
      <p id="mfaPromptText" style="font-size: 13px; margin-bottom: 12px;">Zoom has sent a 6-digit verification code to your email or mobile device.</p>
      <form onsubmit="handleMfaSubmit(event)" style="display: flex; gap: 8px;">
        <input type="text" id="mfaCode" placeholder="Enter 6-digit code" required style="text-align: center; font-size: 18px; letter-spacing: 2px;">
        <button type="submit" class="btn" style="width: auto; padding: 0 20px;">Submit</button>
      </form>
    </div>

    <div id="statusSuccess" class="status-box status-success">
      <svg class="check-icon" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
      <h2 style="font-size: 18px;">Zoom Account Connected!</h2>
      <p id="successAccount" style="font-size: 13px; color: #cbd5e1;"></p>
      <p style="font-size: 14px; margin-top: 6px;">You can now close this tab and return to Telegram. Your session is saved and ready for all future recordings.</p>
    </div>

    <div id="statusFailed" class="status-box status-failed">
      <h3 style="margin-bottom: 4px;">Authentication Failed</h3>
      <p id="failedReason" style="font-size: 13px; margin-bottom: 12px;"></p>
      <button class="btn" onclick="resetForm()" style="background: rgba(255,255,255,0.1); box-shadow: none;">Try Again</button>
    </div>

    <div class="footer-text">
      Zoom Tab Recorder &bull; Telegram Assistant &bull; AES-256 Cloud Session Guard
    </div>
  </div>

  <script>
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    let pollInterval = null;

    if (!token) {
      document.getElementById('mainContainer').innerHTML = `
        <div style="text-align:center; padding: 20px;">
          <h2 style="color: var(--danger); margin-bottom: 8px;">Invalid Link</h2>
          <p style="color: var(--text-muted); font-size: 14px;">This authentication link is missing a valid token. Please return to Telegram and send <code>/login</code> to receive a fresh link.</p>
        </div>
      `;
    }

    function switchTab(mode) {
      if (mode === 'auto') {
        document.getElementById('sectionAuto').style.display = 'block';
        document.getElementById('sectionImport').style.display = 'none';
        document.getElementById('tabAuto').classList.add('active');
        document.getElementById('tabImport').classList.remove('active');
      } else {
        document.getElementById('sectionAuto').style.display = 'none';
        document.getElementById('sectionImport').style.display = 'block';
        document.getElementById('tabImport').classList.add('active');
        document.getElementById('tabAuto').classList.remove('active');
      }
    }

    function hideAllStatus() {
      document.getElementById('statusAuthenticating').style.display = 'none';
      document.getElementById('statusMfa').style.display = 'none';
      document.getElementById('statusSuccess').style.display = 'none';
      document.getElementById('statusFailed').style.display = 'none';
    }

    async function handleCloudLogin(e) {
      e.preventDefault();
      const email = document.getElementById('email').value;
      const password = document.getElementById('password').value;
      document.getElementById('submitBtn').disabled = true;

      hideAllStatus();
      document.getElementById('statusAuthenticating').style.display = 'flex';

      try {
        const res = await fetch('/api/auth/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, email, password })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to initiate login');
        startPolling();
      } catch (err) {
        showFailed(err.message);
      }
    }

    async function handleSessionImport(e) {
      e.preventDefault();
      const rawJson = document.getElementById('sessionJson').value;
      document.getElementById('importBtn').disabled = true;

      hideAllStatus();
      document.getElementById('statusAuthenticating').style.display = 'flex';

      try {
        const res = await fetch('/api/auth/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, storage_state: rawJson })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'SUCCESS') throw new Error(data.error || 'Failed to import session');
        showSuccess(data.zoom_email || 'Zoom Account');
      } catch (err) {
        showFailed(err.message);
      } finally {
        document.getElementById('importBtn').disabled = false;
      }
    }

    async function handleMfaSubmit(e) {
      e.preventDefault();
      const code = document.getElementById('mfaCode').value;
      try {
        await fetch('/api/auth/mfa', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, code })
        });
        hideAllStatus();
        document.getElementById('statusAuthenticating').style.display = 'flex';
      } catch (err) {
        showFailed('Failed to submit 2FA code: ' + err.message);
      }
    }

    function startPolling() {
      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(async () => {
        try {
          const res = await fetch('/api/auth/status?token=' + encodeURIComponent(token));
          if (!res.ok) {
            clearInterval(pollInterval);
            showFailed('Session expired or invalid.');
            return;
          }
          const data = await res.json();
          if (data.status === 'WAITING_MFA') {
            hideAllStatus();
            document.getElementById('statusMfa').style.display = 'block';
            if (data.mfa_prompt) document.getElementById('mfaPromptText').innerText = data.mfa_prompt;
          } else if (data.status === 'SUCCESS') {
            clearInterval(pollInterval);
            showSuccess(data.zoom_email);
          } else if (data.status === 'FAILED') {
            clearInterval(pollInterval);
            showFailed(data.error || 'Authentication failed.');
          }
        } catch (err) {
          console.error(err);
        }
      }, 1500);
    }

    function showSuccess(account) {
      hideAllStatus();
      document.getElementById('sectionAuto').style.display = 'none';
      document.getElementById('sectionImport').style.display = 'none';
      document.querySelector('.tab-toggle').style.display = 'none';
      document.getElementById('statusSuccess').style.display = 'flex';
      if (account) document.getElementById('successAccount').innerText = 'Connected: ' + account;
    }

    function showFailed(reason) {
      hideAllStatus();
      document.getElementById('statusFailed').style.display = 'block';
      document.getElementById('failedReason').innerText = reason;
      document.getElementById('submitBtn').disabled = false;
    }

    function resetForm() {
      hideAllStatus();
      document.getElementById('submitBtn').disabled = false;
    }
  </script>
</body>
</html>
"""


async def handle_root(request: web.Request) -> web.Response:
    """Root endpoint for keep-alive pings."""
    return web.Response(
        text="Zoom Tab Recorder is ONLINE. Keep-alive ping acknowledged.",
        content_type="text/plain",
    )


async def handle_health(request: web.Request) -> web.Response:
    """Detailed JSON health status."""
    try:
        stats = await get_system_health_stats()
        return web.json_response({
            "status": "healthy",
            "service": "zoom-telegram-recorder",
            "active_jobs": stats.get("active_jobs", 0),
            "total_jobs": stats.get("total_jobs", 0),
            "total_recordings": stats.get("total_recordings", 0),
        })
    except Exception as exc:
        logger.error("Health check error: %s", exc)
        return web.json_response({"status": "degraded", "error": str(exc)}, status=500)


async def handle_login_page(request: web.Request) -> web.Response:
    """Serve the mobile Zoom authentication page."""
    token = request.query.get("token", "")
    login_mgr = get_login_session_manager()
    session = login_mgr.get_session(token) if token else None

    if not token or not session:
        return web.Response(
            text="""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#090d16;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
            <div style="text-align:center;max-width:380px;padding:24px;background:#1e293b;border-radius:16px;">
              <h2 style="color:#ef4444;margin-bottom:8px;">⚠️ Invalid or Expired Link</h2>
              <p style="color:#94a3b8;font-size:14px;">This login portal link has expired. Return to your Telegram chat and send <code>/login</code> to generate a fresh connection link.</p>
            </div></body></html>""",
            content_type="text/html",
            status=401,
        )

    return web.Response(text=LOGIN_HTML_TEMPLATE, content_type="text/html")


async def handle_api_submit(request: web.Request) -> web.Response:
    """Initiate cloud browser authentication with submitted credentials."""
    try:
        data = await request.json()
        token = data.get("token", "").strip()
        email = data.get("email", "").strip()
        password = data.get("password", "")

        if not token or not email or not password:
            return web.json_response({"error": "Missing token, email, or password."}, status=400)

        login_mgr = get_login_session_manager()
        session = login_mgr.get_session(token)
        if not session:
            return web.json_response({"error": "Authentication session expired or invalid."}, status=404)

        await login_mgr.start_cloud_login(token, email, password)
        return web.json_response({"status": "AUTHENTICATING"})

    except Exception as exc:
        logger.error("API submit error: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def handle_api_mfa(request: web.Request) -> web.Response:
    """Submit 2FA verification code to cloud browser."""
    try:
        data = await request.json()
        token = data.get("token", "").strip()
        code = data.get("code", "").strip()

        if not token or not code:
            return web.json_response({"error": "Missing token or verification code."}, status=400)

        login_mgr = get_login_session_manager()
        success = await login_mgr.submit_mfa_code(token, code)
        if not success:
            return web.json_response({"error": "Session is not awaiting MFA code."}, status=400)

        return web.json_response({"status": "CODE_FORWARDED"})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_api_import(request: web.Request) -> web.Response:
    """Import existing browser session state."""
    try:
        data = await request.json()
        token = data.get("token", "").strip()
        raw_storage = data.get("storage_state")

        login_mgr = get_login_session_manager()
        success, msg = await login_mgr.import_session_state(token, raw_storage)
        if not success:
            return web.json_response({"error": msg}, status=400)

        session = login_mgr.get_session(token)
        return web.json_response({
            "status": "SUCCESS",
            "zoom_email": session.zoom_email if session else "Zoom User",
        })
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_api_status(request: web.Request) -> web.Response:
    """Poll authentication status for mobile web portal."""
    token = request.query.get("token", "").strip()
    login_mgr = get_login_session_manager()
    session = login_mgr.get_session(token)

    if not session:
        return web.json_response({"error": "Session expired or invalid."}, status=404)

    return web.json_response({
        "status": session.status,
        "error": session.error_message,
        "mfa_prompt": session.mfa_prompt,
        "zoom_email": session.zoom_email,
    })


OAUTH_SUCCESS_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Zoom Connected &bull; Cloud Recorder</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(22, 30, 49, 0.9);
      --card-border: rgba(16, 185, 129, 0.25);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --success: #10b981;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      background-image: 
        radial-gradient(at 0% 0%, rgba(16, 185, 129, 0.2) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(37, 99, 235, 0.15) 0px, transparent 50%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .container {
      width: 100%;
      max-width: 440px;
      background: var(--card-bg);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--card-border);
      border-radius: 24px;
      padding: 36px 28px;
      text-align: center;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
    }
    .badge-icon {
      width: 64px;
      height: 64px;
      border-radius: 50%;
      background: linear-gradient(135deg, #10b981 0%, #059669 100%);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 20px;
      box-shadow: 0 10px 25px -5px rgba(16, 185, 129, 0.5);
    }
    .badge-icon svg { width: 34px; height: 34px; fill: white; }
    h1 { font-size: 22px; font-weight: 700; margin-bottom: 8px; color: #f8fafc; }
    p.desc { font-size: 14px; color: var(--text-muted); line-height: 1.6; margin-bottom: 24px; }
    .info-card {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 14px;
      padding: 14px 16px;
      margin-bottom: 24px;
      font-size: 13px;
      color: #cbd5e1;
      text-align: left;
    }
    .info-row { display: flex; justify-content: space-between; padding: 4px 0; }
    .info-label { color: var(--text-muted); }
    .info-val { font-weight: 600; color: #f8fafc; }
    .close-btn {
      display: block;
      width: 100%;
      padding: 14px;
      background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
      color: white;
      text-decoration: none;
      border-radius: 12px;
      font-weight: 600;
      font-size: 15px;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="badge-icon">
      <svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
    </div>
    <h1>Zoom Account Connected</h1>
    <p class="desc">Your Zoom account has been securely authorized via official OAuth 2.0. You can now close this tab and return to Telegram.</p>
    <div class="info-card">
      <div class="info-row"><span class="info-label">Account:</span><span class="info-val">{email}</span></div>
      <div class="info-row"><span class="info-label">Status:</span><span class="info-val" style="color:#10b981;">Authorized & Active</span></div>
      <div class="info-row"><span class="info-label">Authorization:</span><span class="info-val">OAuth 2.0 (user:read)</span></div>
    </div>
    <a href="https://t.me/" class="close-btn" onclick="window.close();">Return to Telegram</a>
  </div>
</body>
</html>"""


OAUTH_ERROR_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Connection Notice &bull; Cloud Recorder</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(22, 30, 49, 0.9);
      --card-border: rgba(239, 68, 68, 0.25);
      --text: #f8fafc;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .container {
      width: 100%;
      max-width: 440px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 24px;
      padding: 36px 28px;
      text-align: center;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
    }
    .badge-icon {
      width: 64px;
      height: 64px;
      border-radius: 50%;
      background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 20px;
      box-shadow: 0 10px 25px -5px rgba(239, 68, 68, 0.5);
    }
    .badge-icon svg { width: 34px; height: 34px; fill: white; }
    h1 { font-size: 22px; font-weight: 700; margin-bottom: 8px; }
    p.desc { font-size: 14px; color: var(--text-muted); line-height: 1.6; margin-bottom: 24px; }
    .btn {
      display: block;
      width: 100%;
      padding: 14px;
      background: rgba(255, 255, 255, 0.1);
      color: white;
      text-decoration: none;
      border-radius: 12px;
      font-weight: 600;
      font-size: 15px;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="badge-icon">
      <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
    </div>
    <h1>{title}</h1>
    <p class="desc">{message}</p>
    <a href="https://t.me/" class="btn" onclick="window.close();">Return to Telegram</a>
  </div>
</body>
</html>"""


def _render_oauth_success_page(email: str) -> str:
    return OAUTH_SUCCESS_HTML_TEMPLATE.replace("{email}", email)


def _render_oauth_error_page(title: str, message: str) -> str:
    return OAUTH_ERROR_HTML_TEMPLATE.replace("{title}", title).replace("{message}", message)


async def handle_oauth_callback(request: web.Request) -> web.Response:
    """Official Zoom OAuth 2.0 callback endpoint.

    Validates state against CSRF attacks, exchanges authorization code for tokens,
    retrieves user profile info, persists encrypted tokens at rest, and confirms via Telegram.
    """
    import time
    from app.auth import PersistentZoomSessionManager
    from app.database import update_user_zoom_session
    from app.notifications import get_notification_service
    from app.security import mask_email

    error = request.query.get("error")
    error_description = request.query.get("error_description", "Authorization was denied or cancelled.")

    if error:
        logger.info("Zoom OAuth authorization denied by user: %s - %s", error, error_description)
        html = _render_oauth_error_page(
            title="Authorization Cancelled",
            message="You declined or cancelled the Zoom account authorization. Return to Telegram and run <code>/login</code> anytime if you wish to connect."
        )
        return web.Response(text=html, content_type="text/html", status=200)

    code = request.query.get("code", "").strip()
    state = request.query.get("state", "").strip()

    if not code or not state:
        logger.warning("Zoom OAuth callback missing code or state parameter.")
        html = _render_oauth_error_page(
            title="Missing Parameters",
            message="The authorization callback did not include the required code or state parameter. Please run <code>/login</code> again from Telegram."
        )
        return web.Response(text=html, content_type="text/html", status=400)

    oauth_service = get_zoom_oauth_service()
    telegram_user_id = oauth_service.validate_and_consume_state(state)

    if not telegram_user_id:
        logger.warning("Zoom OAuth CSRF state verification failed.")
        html = _render_oauth_error_page(
            title="Security Verification Failed",
            message="Invalid or expired authorization session (CSRF verification failed). Please return to Telegram and send <code>/login</code> to generate a new secure connection link."
        )
        return web.Response(text=html, content_type="text/html", status=403)

    try:
        # Exchange authorization code for tokens
        token_data = await oauth_service.exchange_code_for_tokens(code)

        # Retrieve user profile
        user_profile = await oauth_service.get_user_profile(token_data["access_token"])
        token_data["user_profile"] = user_profile
        token_data["expires_at"] = time.time() + token_data.get("expires_in", 3600)
        token_data["connected_at"] = time.time()

        # Persist encrypted tokens
        session_mgr = PersistentZoomSessionManager()
        await session_mgr.save_oauth_tokens_async(telegram_user_id, token_data)

        # Update database record
        raw_email = user_profile.get("email")
        masked = mask_email(raw_email) if raw_email else "Zoom Account"
        await update_user_zoom_session(telegram_user_id, zoom_email=masked, status="CONNECTED")

        # Notify user on Telegram
        notifier = get_notification_service()
        if notifier:
            first_name = user_profile.get("first_name", "")
            last_name = user_profile.get("last_name", "")
            name = f"{first_name} {last_name}".strip() or "Zoom User"
            account_id = user_profile.get("account_id") or "N/A"
            await notifier.send_custom_message(
                chat_id=telegram_user_id,
                message_text=(
                    "🎉 *Zoom Account Authorized Successfully!*\n\n"
                    f"Your Zoom account (`{masked}`) is now connected via official Zoom OAuth 2.0.\n\n"
                    f"• *Authorized User:* {name}\n"
                    f"• *Account ID:* `{account_id}`\n"
                    f"• *Status:* Active & Connected\n"
                    f"• *Permissions:* User Identity (`user:read`)\n"
                    f"• *Storage:* AES-256 Encrypted Session\n\n"
                    "Your connection is active and ready for scheduled recordings."
                ),
            )

        logger.info("Successfully connected Zoom account for user %d via official OAuth 2.0", telegram_user_id)
        html = _render_oauth_success_page(email=masked)
        return web.Response(text=html, content_type="text/html", status=200)

    except ZoomOAuthError as exc:
        logger.error("Zoom OAuth callback processing failed: %s", exc)
        html = _render_oauth_error_page(
            title="Authorization Failed",
            message=f"Unable to complete Zoom OAuth authorization: {str(exc)}. Please try again via <code>/login</code>."
        )
        return web.Response(text=html, content_type="text/html", status=400)
    except Exception as exc:
        logger.error("Unexpected error handling Zoom OAuth callback: %s", exc, exc_info=True)
        html = _render_oauth_error_page(
            title="System Error",
            message="An unexpected error occurred during authorization. Please try again."
        )
        return web.Response(text=html, content_type="text/html", status=500)


def create_health_app() -> web.Application:
    """Construct aiohttp web application with health probes and login endpoints."""
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ping", handle_root)
    app.router.add_get("/oauth/callback", handle_oauth_callback)
    app.router.add_get("/login", handle_login_page)
    app.router.add_post("/api/auth/submit", handle_api_submit)
    app.router.add_post("/api/auth/mfa", handle_api_mfa)
    app.router.add_post("/api/auth/import", handle_api_import)
    app.router.add_get("/api/auth/status", handle_api_status)
    return app


class KeepAliveServer:
    """Manages the background HTTP server for keep-alive pings and the mobile auth portal."""

    def __init__(self, port: int = 8080):
        self.port = port
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    async def start(self) -> None:
        """Start listening for incoming requests."""
        app = create_health_app()
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await self.site.start()
        logger.info("Keep-alive & auth HTTP server listening on http://0.0.0.0:%d", self.port)

    async def stop(self) -> None:
        """Gracefully stop the server."""
        if self.runner:
            logger.info("Stopping keep-alive HTTP server...")
            await self.runner.cleanup()
            self.runner = None
            self.site = None
