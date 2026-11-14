"""Syntactic URL validation for enqueue-time checks."""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urlparse

from app.core.errors import APIError

_BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal", "metadata.internal"})
_LOCALHOST_SUFFIX = ".localhost"


def _reject(message: str) -> None:
    raise APIError(400, "validation_error", message)


def canonicalize_url(url: str) -> str:
    trimmed = (url or "").strip()
    if not trimmed:
        _reject("A URL is required.")
    without_fragment, _fragment = urldefrag(trimmed)
    parsed = urlparse(without_fragment)
    if parsed.scheme not in {"http", "https"}:
        _reject("Only HTTP and HTTPS URLs are allowed.")
    if not parsed.hostname:
        _reject("The URL must include a hostname.")
    if parsed.username or parsed.password:
        _reject("URLs with embedded credentials are not allowed.")
    if parsed.params:
        _reject("The URL is not allowed.")

    hostname = parsed.hostname.lower()
    if hostname in _BLOCKED_HOSTS or hostname.endswith(_LOCALHOST_SUFFIX):
        _reject("The URL hostname is not allowed.")

    if parsed.port not in (None, 80, 443):
        _reject("Only ports 80 and 443 are allowed.")

    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{parsed.hostname}:{port}{path}{query}"


def validate_url_for_enqueue(url: str) -> str:
    canonical = canonicalize_url(url)
    host = urlparse(canonical).hostname or ""
    try:
        host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        _reject("The URL is not valid.")
    if re.search(r"\s", url):
        _reject("The URL is not valid.")
    return canonical