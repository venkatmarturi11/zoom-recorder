# Security & Ethical Compliance Checklist

This project is built from the ground up to guarantee strict security, user isolation, data privacy, and ethical compliance.

---

## 1. Authentication & Authorization

- [x] **Telegram User Allowlisting:** Only user IDs listed in `ALLOWED_TELEGRAM_USER_IDS` can trigger commands or receive recordings. Unregistered users are blocked and alerted.
- [x] **Admin Role Separation:** Destructive administrative actions and system metrics are restricted to `ADMIN_USER_IDS`.
- [x] **No Plaintext Passwords:** Zoom meeting passcodes and credentials are never stored in plaintext in the database or written to terminal logs.

---

## 2. Network & SSRF Defense

- [x] **Domain Whitelisting:** The recorder only permits legitimate Zoom endpoints (`*.zoom.us` and `*.zoomgov.com`).
- [x] **SSRF Protection:** Hostnames are verified to ensure they do not resolve to private RFC1918 subnets (`10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`), loopback (`127.0.0.1`), or cloud instance metadata services (`169.254.169.254`).
- [x] **Restricted Protocols:** Only `http` and `https` protocols are accepted; `file://`, `ftp://`, or `gopher://` are rejected.

---

## 3. Persistent Session Encryption

- [x] **Fernet AES-256 Storage:** Stored browser session cookies (`storage_state.json`) are encrypted at rest using AES-256-CBC with HMAC authentication derived from `APP_SECRET_KEY`.
- [x] **User Isolation:** Each user's browser session state is partitioned in isolated encrypted files (`session_user_<id>.enc`) to prevent cross-account leakage.
- [x] **Re-authentication Prompts:** If an authenticated session expires or MFA is demanded, the bot prompts the administrator rather than attempting to bypass security checks.

---

## 4. Log Redaction & Observability

- [x] **Automatic Redacting Formatter:** Structured log outputs automatically scrub sensitive credentials:
  - Zoom meeting passcodes (`pwd=...`) -> `[REDACTED_PASSCODE]`
  - Telegram bot tokens (`123456:ABC...`) -> `[REDACTED_TELEGRAM_TOKEN]`
  - AWS/S3 access keys & secrets -> `[REDACTED_AWS_SECRET]`
  - Bearer tokens & signature hashes -> `[REDACTED_AUTH_TOKEN]`
- [x] **Safe Error Messages:** Exceptions returned to Telegram chats provide helpful troubleshooting guidance without leaking server hostnames, internal IPs, or stack traces.

---

## 5. Ethical & Authorized Recording Principles

- [x] **Transparent Bot Identity:** The bot joins Zoom meetings with an explicit, recognizable display name: `Zoom Recorder Bot (Authorized Assistant)`.
- [x] **No Bypass of Host Controls:** The worker respects Zoom Waiting Rooms, host admittance, meeting passcodes, and registration forms.
- [x] **No Secret Recording:** The recording subsystem does not circumvent Zoom recording notifications or tamper with participant list visibility.
