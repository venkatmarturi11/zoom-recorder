"""Structured and secure logging configuration with automatic sensitive data redaction."""

import logging
import re
import sys
from typing import Any


class RedactingFormatter(logging.Formatter):
    """Custom formatter that automatically scrubs sensitive credentials from log outputs."""

    # Patterns to match sensitive tokens, passwords, and authorization headers
    REDACT_PATTERNS = [
        # Zoom passcodes: pwd=xxxx
        (re.compile(r"(pwd=)([^&\s]+)", re.IGNORECASE), r"\1[REDACTED_PASSCODE]"),
        # Telegram Bot tokens: 123456:ABC-DEF...
        (re.compile(r"\b(\d{8,10}:[A-Za-z0-9_-]{35})\b"), "[REDACTED_TELEGRAM_TOKEN]"),
        # AWS / S3 Secret Access Keys: 40-character base64/alphanumeric strings
        (re.compile(r"(AWS_SECRET_ACCESS_KEY|aws_secret|SecretKey|secret_key)[\s:=]+['\"]?([A-Za-z0-9/+=]{40})['\"]?", re.IGNORECASE), r"\1=[REDACTED_AWS_SECRET]"),
        # AWS Access Key IDs: AKIA... / ASIA...
        (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED_AWS_KEY_ID]"),
        # Zoom meeting signature or zpk tokens
        (re.compile(r"(zak=|tk=|zpk=)([^&\s]+)", re.IGNORECASE), r"\1[REDACTED_AUTH_TOKEN]"),
        # Bearer tokens
        (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), r"\1[REDACTED_BEARER_TOKEN]"),
    ]

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        redacted = original
        for pattern, replacement in self.REDACT_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the application root logger with redacting formatters."""
    logger = logging.getLogger("zoom_recorder")
    logger.setLevel(level)

    # Avoid duplicate handlers on re-initialization
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = RedactingFormatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s:%(module)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Configure third-party loggers to avoid excessive noise
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("playwright").setLevel(logging.INFO)
    logging.getLogger("botocore").setLevel(logging.WARNING)

    return logger
