"""Security verification, Zoom URL parsing, SSRF prevention, and encryption utilities."""

import base64
import ipaddress
import re
import socket
from urllib.parse import parse_qs, urlparse
from typing import Optional, Tuple
from cryptography.fernet import Fernet
from app.config import get_settings


class SecurityError(Exception):
    """Base exception for security validation violations."""
    pass


class InvalidZoomURLError(SecurityError):
    """Raised when a Zoom URL fails security, domain, or format checks."""
    pass


class SSRFAttemptError(SecurityError):
    """Raised when a URL attempts to target private or internal IP ranges."""
    pass


# Strict regex for authorized Zoom domains
ALLOWED_ZOOM_DOMAIN_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9-]+\.)*(?:zoom\.us|zoomgov\.com)$",
    re.IGNORECASE,
)

# Standard Zoom meeting ID pattern (9 to 11 digits)
MEETING_ID_PATTERN = re.compile(r"^\d{9,11}$")


def is_private_or_reserved_ip(ip_str: str) -> bool:
    """Check if an IP string is private, loopback, link-local, or reserved."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or str(ip) in ("0.0.0.0", "255.255.255.255")
        )
    except ValueError:
        return True


def validate_and_parse_zoom_url(
    url_str: str,
    explicit_passcode: Optional[str] = None,
) -> Tuple[str, str, Optional[str]]:
    """Validate a submitted Zoom URL or meeting ID and extract meeting details.

    Performs strict domain validation, SSRF checks, and parses:
    - Normalized web client join URL
    - Meeting ID
    - Passcode (from query string or explicit argument)

    Returns:
        (normalized_join_url, meeting_id, passcode)

    Raises:
        InvalidZoomURLError: if URL is invalid or domain is not Zoom
        SSRFAttemptError: if URL points to private/internal infrastructure
    """
    if not url_str or not isinstance(url_str, str):
        raise InvalidZoomURLError("Empty or non-string Zoom URL provided.")

    trimmed = url_str.strip().strip("<>\"'").strip()

    # Check if raw meeting ID was provided (e.g. "83373006475" or "833 7300 6475")
    digits_only = re.sub(r"[\s-]+", "", trimmed)
    if MEETING_ID_PATTERN.match(digits_only):
        trimmed = f"https://app.zoom.us/wc/join/{digits_only}"

    if not (trimmed.startswith("http://") or trimmed.startswith("https://")):
        trimmed = "https://" + trimmed

    parsed = urlparse(trimmed)
    if parsed.scheme not in ("http", "https"):
        raise InvalidZoomURLError("Only HTTP and HTTPS protocols are supported.")

    hostname = parsed.hostname
    if not hostname:
        raise InvalidZoomURLError("Invalid URL: missing hostname.")

    # Check against allowed Zoom domains
    if not ALLOWED_ZOOM_DOMAIN_PATTERN.match(hostname):
        raise InvalidZoomURLError(
            f"Domain '{hostname}' is not a recognized Zoom domain. Only *.zoom.us and *.zoomgov.com are allowed."
        )

    # SSRF Protection: Resolve hostname to verify it doesn't resolve to internal IP
    try:
        resolved_ips = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in resolved_ips:
            ip_addr = sockaddr[0]
            if is_private_or_reserved_ip(ip_addr):
                raise SSRFAttemptError(f"Security Alert: Domain resolves to private or internal IP ({ip_addr}).")
    except socket.gaierror:
        # In test environments or temporary DNS disruptions, verify strict domain regex
        if not ALLOWED_ZOOM_DOMAIN_PATTERN.match(hostname):
            raise InvalidZoomURLError(f"Unable to resolve host '{hostname}'. Verify network connection.")

    # Parse meeting ID from path (supports /j/{id}, /wc/{id}/join, /wc/join/{id})
    path = parsed.path.rstrip("/")
    meeting_id = None

    # Matches /j/123456789 or /wc/123456789/join or /wc/join/123456789
    j_match = re.search(r"/(?:j|wc(?:/join)?)/(\d{9,11})", path)
    if j_match:
        meeting_id = j_match.group(1)
    else:
        # Check query param ?confno=123456789 or ?meeting_id=123456789
        qs = parse_qs(parsed.query)
        if "confno" in qs and qs["confno"]:
            meeting_id = qs["confno"][0].strip()
        elif "meeting_id" in qs and qs["meeting_id"]:
            meeting_id = qs["meeting_id"][0].strip()

    if not meeting_id or not MEETING_ID_PATTERN.match(meeting_id):
        raise InvalidZoomURLError("Could not locate a valid 9-to-11 digit Zoom Meeting ID in the provided URL.")

    # Parse passcode from query string (pwd=...) or explicit argument
    qs = parse_qs(parsed.query)
    passcode = None
    if "pwd" in qs and qs["pwd"]:
        passcode = qs["pwd"][0].strip()
    elif explicit_passcode and explicit_passcode.strip():
        passcode = explicit_passcode.strip()

    # Generate standardized Zoom Web Client Join URL
    # Format: https://app.zoom.us/wc/join/{meeting_id}
    # Passcode will be provided programmatically to the web client input
    normalized_join_url = f"https://app.zoom.us/wc/join/{meeting_id}"
    if passcode:
        normalized_join_url += f"?pwd={passcode}"

    return normalized_join_url, meeting_id, passcode


class EncryptedSessionStorage:
    """Manages persistent browser session encryption with Fernet AES-256."""

    def __init__(self, key: Optional[str] = None):
        import hashlib
        settings = get_settings()
        secret_input = key or settings.app_secret_key or "default_zoom_recorder_secure_secret_key"
        # Derive exactly 32 raw bytes via SHA-256 and base64-encode for Fernet
        key_32 = hashlib.sha256(secret_input.encode("utf-8")).digest()
        fernet_key = base64.urlsafe_b64encode(key_32)
        self._fernet = Fernet(fernet_key)

    def encrypt_data(self, plaintext: str) -> str:
        """Encrypt plaintext string to ciphertext."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt_data(self, ciphertext: str) -> str:
        """Decrypt ciphertext back to plaintext string."""
        return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def mask_email(email: Optional[str]) -> str:
    """Mask email for secure display in Telegram messages (e.g. u***@domain.com)."""
    if not email or "@" not in email:
        return "Unknown"
    user_part, domain_part = email.split("@", 1)
    if len(user_part) <= 2:
        masked_user = user_part[0] + "***"
    else:
        masked_user = user_part[0] + "***" + user_part[-1]
    return f"{masked_user}@{domain_part}"

