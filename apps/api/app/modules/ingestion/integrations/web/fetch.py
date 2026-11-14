"""Pinned HTTP fetch without automatic redirects."""

from __future__ import annotations

import http.client
import ssl
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx

from app.domain.extraction import ExtractionError
from app.modules.ingestion.integrations.web.addresses import validate_hostname
from app.modules.ingestion.url_validation import canonicalize_url

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 15
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/plain",
    }
)


@dataclass(slots=True)
class FetchResponse:
    url: str
    final_url: str
    status_code: int
    body: bytes
    content_type: str
    resolved_addresses: list[str]


ResolveFn = Callable[[str], tuple[list[str], list[str]]]


def _unsupported_type(message: str) -> ExtractionError:
    return ExtractionError("unsupported_type", message)


def _redirect_limit(message: str) -> ExtractionError:
    return ExtractionError("redirect_limit", message)


def _fetch_failed(message: str) -> ExtractionError:
    return ExtractionError("fetch_failed", message)


def _content_type(headers: http.client.HTTPMessage) -> str:
    raw = headers.get("Content-Type", "")
    return raw.split(";", 1)[0].strip().lower()


def _pinned_get(
    *,
    url: str,
    hostname: str,
    pinned_ip: str,
    timeout: int,
) -> tuple[int, bytes, http.client.HTTPMessage]:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    if parsed.scheme == "https":
        context = ssl.create_default_context()
        conn = http.client.HTTPSConnection(
            pinned_ip,
            port,
            timeout=timeout,
            context=context,
            server_hostname=hostname,
        )
    else:
        conn = http.client.HTTPConnection(pinned_ip, port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Host": hostname, "User-Agent": "RAGFlow/1.0"})
        response = conn.getresponse()
        chunks: list[bytes] = []
        total = 0
        while True:
            block = response.read(64 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_BODY_BYTES:
                raise _unsupported_type("The response body exceeds the 5 MB limit.")
            chunks.append(block)
        return response.status, b"".join(chunks), response
    except ExtractionError:
        raise
    except TimeoutError as exc:
        raise _fetch_failed("The request timed out.") from exc
    except OSError as exc:
        raise _fetch_failed("The request could not be completed.") from exc
    finally:
        conn.close()


def fetch_url(
    url: str,
    *,
    resolve: ResolveFn | None = None,
    transport: httpx.BaseTransport | None = None,
    cookies: dict[str, str] | None = None,
) -> FetchResponse:
    current = canonicalize_url(url)
    cookies = dict(cookies or {})
    redirects = 0
    transport_client: httpx.Client | None = None
    if transport is not None:
        transport_client = httpx.Client(
            transport=transport,
            timeout=READ_TIMEOUT,
            follow_redirects=False,
            cookies=cookies,
        )
    try:
        while True:
            parsed = urlparse(current)
            hostname = parsed.hostname or ""
            addresses = validate_hostname(hostname, resolve=resolve)

            if transport_client is not None:
                try:
                    response = transport_client.get(
                        current,
                        headers={"User-Agent": "RAGFlow/1.0"},
                    )
                except httpx.TimeoutException as exc:
                    raise _fetch_failed("The request timed out.") from exc
                except httpx.HTTPError as exc:
                    raise _fetch_failed("The request could not be completed.") from exc
                body = response.content
                if len(body) > MAX_BODY_BYTES:
                    raise _unsupported_type("The response body exceeds the 5 MB limit.")
                status = response.status_code
                headers = response.headers
                content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            else:
                status, body, raw_headers = _pinned_get(
                    url=current,
                    hostname=hostname,
                    pinned_ip=addresses[0],
                    timeout=CONNECT_TIMEOUT + READ_TIMEOUT,
                )
                content_type = _content_type(raw_headers)
                headers = raw_headers

            if status in {301, 302, 303, 307, 308}:
                if redirects >= MAX_REDIRECTS:
                    raise _redirect_limit("The redirect chain exceeds the limit.")
                if isinstance(headers, httpx.Headers):
                    location = headers.get("Location")
                else:
                    location = headers.get("Location")
                if not location:
                    raise _fetch_failed("The redirect response did not include a location.")
                redirects += 1
                current = canonicalize_url(urljoin(current, location))
                continue

            if status >= 400:
                raise _fetch_failed(f"The server responded with status {status}.")

            if content_type not in ALLOWED_CONTENT_TYPES:
                raise _unsupported_type("The response content type is not supported.")

            return FetchResponse(
                url=url,
                final_url=current,
                status_code=status,
                body=body,
                content_type=content_type,
                resolved_addresses=addresses,
            )
    finally:
        if transport_client is not None:
            transport_client.close()