"""Shared helpers: logging setup, HTTP session, file saving."""

import logging
import sys
from pathlib import Path

import httpx

from src.config import TIMEOUT_SECONDS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Return a logger with a concise console format.

    Uses UTF-8 output stream to avoid UnicodeEncodeError on Windows
    when logging Hebrew or other non-ASCII text.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        # Force UTF-8 on Windows (default cp1252 can't encode Hebrew)
        utf8_stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        handler = logging.StreamHandler(utf8_stdout)
        handler.setFormatter(
            logging.Formatter("[%(levelname)s] %(name)s | %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def make_client() -> httpx.Client:
    """Create a reusable httpx client with reasonable defaults.

    - Transport-level retries for transient connection errors
    - Limited connection pool to avoid resource exhaustion on long runs
    - Short keepalive to prevent stale connections over hours
    """
    transport = httpx.HTTPTransport(retries=2)
    return httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=15.0),
        follow_redirects=True,
        headers={"User-Agent": "HotelStaticDataChecker/1.0"},
        transport=transport,
        limits=httpx.Limits(
            max_connections=10,
            max_keepalive_connections=5,
            keepalive_expiry=30.0,
        ),
    )


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def save_html(directory: Path, filename: str, html: str) -> Path:
    """Persist raw HTML for debugging; return the saved path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(html, encoding="utf-8")
    return path
