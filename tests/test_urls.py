"""Automated tests for Zoom URL validation and SSRF prevention."""

import pytest
from app.security import (
    InvalidZoomURLError,
    SSRFAttemptError,
    is_private_or_reserved_ip,
    validate_and_parse_zoom_url,
)


def test_valid_zoom_urls():
    """Verify legitimate Zoom URLs parse meeting ID, passcode, and normalized URL."""
    # Standard join URL with passcode
    url1 = "https://us02web.zoom.us/j/1234567890?pwd=secretpassword"
    norm1, mid1, pwd1 = validate_and_parse_zoom_url(url1)
    assert mid1 == "1234567890"
    assert pwd1 == "secretpassword"
    assert "https://app.zoom.us/wc/join/1234567890" in norm1
    assert "pwd=secretpassword" in norm1

    # Gov domain without passcode
    url2 = "https://myagency.zoomgov.com/j/98765432109"
    norm2, mid2, pwd2 = validate_and_parse_zoom_url(url2)
    assert mid2 == "98765432109"
    assert pwd2 is None
    assert norm2 == "https://app.zoom.us/wc/join/98765432109"

    # Web client URL (/wc/join/id)
    url3 = "https://zoom.us/wc/join/876543210"
    norm3, mid3, pwd3 = validate_and_parse_zoom_url(url3)
    assert mid3 == "876543210"

    # Query string confno
    url4 = "https://zoom.us/j?confno=1122334455&pwd=test"
    norm4, mid4, pwd4 = validate_and_parse_zoom_url(url4)
    assert mid4 == "1122334455"
    assert pwd4 == "test"


def test_invalid_domains():
    """Ensure non-Zoom domains are rejected."""
    invalid_domains = [
        "https://evil-zoom.us/j/1234567890",
        "https://zoom.us.attacker.com/j/1234567890",
        "https://google.com/j/1234567890",
        "https://teams.microsoft.com/j/1234567890",
        "http://notzoom.com/1234567890",
    ]
    for url in invalid_domains:
        with pytest.raises(InvalidZoomURLError):
            validate_and_parse_zoom_url(url)


def test_malformed_urls():
    """Ensure invalid schemes and missing meeting IDs raise InvalidZoomURLError."""
    with pytest.raises(InvalidZoomURLError):
        validate_and_parse_zoom_url("ftp://zoom.us/j/1234567890")

    with pytest.raises(InvalidZoomURLError):
        validate_and_parse_zoom_url("https://zoom.us/about")

    with pytest.raises(InvalidZoomURLError):
        validate_and_parse_zoom_url("not a url")

    with pytest.raises(InvalidZoomURLError):
        validate_and_parse_zoom_url("")


def test_private_ip_detection():
    """Verify SSRF IP filter correctly identifies loopback, private, and link-local addresses."""
    assert is_private_or_reserved_ip("127.0.0.1") is True
    assert is_private_or_reserved_ip("10.0.0.1") is True
    assert is_private_or_reserved_ip("192.168.1.1") is True
    assert is_private_or_reserved_ip("172.16.0.1") is True
    assert is_private_or_reserved_ip("169.254.169.254") is True  # Cloud metadata
    assert is_private_or_reserved_ip("0.0.0.0") is True
    assert is_private_or_reserved_ip("::1") is True

    # Public IP
    assert is_private_or_reserved_ip("8.8.8.8") is False
    assert is_private_or_reserved_ip("1.1.1.1") is False
