"""URL ingestion validation, SSRF policy, fetch, and worker tests."""

from __future__ import annotations

import httpx
import pytest

pytest_plugins = ["test_canonical_blocks"]

from app.core.errors import APIError
from app.domain.extraction import ExtractionError
from app.modules.ingestion.integrations.web.crawl import crawl_and_extract
from app.modules.ingestion.integrations.web.fetch import MAX_REDIRECTS, fetch_url
from app.modules.ingestion.url_validation import validate_url_for_enqueue
from tests.test_canonical_blocks import (  # noqa: PLC0415 — shared harness
        INDEX_STAGE_TAIL,
    _block_count,
    _enqueue,
    _events,
    _execute,
)

_SEED_HTML = b"""<html><body>
<h1>Docs</h1>
<p>Welcome</p>
<a href="https://example.com/child">Child</a>
</body></html>"""


def _resolve_public(_host: str) -> tuple[list[str], list[str]]:
    return (["93.184.216.34"], [])


def _resolve_loopback(_host: str) -> tuple[list[str], list[str]]:
    return (["127.0.0.1"], [])


def _resolve_mixed(_host: str) -> tuple[list[str], list[str]]:
    return (["93.184.216.34"], ["10.0.0.1"])


def test_validate_url_rejects_userinfo_and_file_scheme() -> None:
    with pytest.raises(APIError) as userinfo:
        validate_url_for_enqueue("http://user:pass@example.com/")
    assert userinfo.value.code == "validation_error"

    with pytest.raises(APIError) as file_url:
        validate_url_for_enqueue("file:///etc/passwd")
    assert file_url.value.code == "validation_error"


def test_validate_url_rejects_nonstandard_port() -> None:
    with pytest.raises(APIError) as exc:
        validate_url_for_enqueue("http://example.com:8080/")
    assert exc.value.code == "validation_error"


@pytest.mark.parametrize(
    ("url", "resolve"),
    [
        ("http://127.0.0.1/", _resolve_public),
        ("http://10.1.2.3/", _resolve_public),
        ("http://169.254.1.1/", _resolve_public),
        ("http://169.254.169.254/", _resolve_public),
        ("http://blocked.test/", _resolve_loopback),
        ("http://mixed.test/", _resolve_mixed),
    ],
)
def test_fetch_ssrf_blocked(url: str, resolve) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok", headers={"Content-Type": "text/plain"}))
    with pytest.raises(ExtractionError) as exc:
        fetch_url(url, resolve=resolve, transport=transport)
    assert exc.value.code == "ssrf_blocked"


def test_fetch_redirect_to_blocked_target_is_ssrf() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
        return httpx.Response(200, content=b"oops", headers={"Content-Type": "text/plain"})

    with pytest.raises(ExtractionError) as exc:
        fetch_url(
            "http://example.com/",
            resolve=_resolve_public,
            transport=httpx.MockTransport(transport),
        )
    assert exc.value.code == "ssrf_blocked"


def test_fetch_redirect_limit_on_sixth_hop() -> None:
    count = {"n": 0}

    def transport(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        return httpx.Response(302, headers={"Location": "http://example.com/next"})

    with pytest.raises(ExtractionError) as exc:
        fetch_url(
            "http://example.com/",
            resolve=_resolve_public,
            transport=httpx.MockTransport(transport),
        )
    assert exc.value.code == "redirect_limit"
    assert count["n"] == MAX_REDIRECTS + 1


def test_crawl_extracts_blocks_with_url_locators() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SEED_HTML, headers={"Content-Type": "text/html"})

    result = crawl_and_extract(
        seed_url="https://example.com/docs",
        max_pages=1,
        depth=0,
        resolve=_resolve_public,
        transport=httpx.MockTransport(transport),
    )
    blocks = result.blocks
    assert blocks[0].block_type == "heading"
    assert blocks[0].locator["url"] == "https://example.com:443/docs"
    assert blocks[0].locator["heading_path"] == ["Docs"]
    assert blocks[1].text == "Welcome"


def test_url_api_rejects_before_enqueue(client, kb_ctx: dict) -> None:
    from app.core.db import SessionLocal
    from app.models import User

    with SessionLocal() as session:
        user = session.get(User, kb_ctx["user_id"])
        assert user is not None
        email = user.email
    token = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password-long-enough"},
    ).json()["access_token"]
    response = client.post(
        f"/api/v1/knowledge-bases/{kb_ctx['kb_id']}/sources/url",
        json={"url": "http://user:pass@example.com/"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


def test_worker_ingests_web_into_canonical_blocks(kb_ctx: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.db import SessionLocal
    from app.models import Job, ProcessingRun

    from app.modules.ingestion.url_service import enqueue_url_source

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SEED_HTML, headers={"Content-Type": "text/html"})

    def patched_crawl(**kwargs):
        return crawl_and_extract(
            **kwargs,
            resolve=_resolve_public,
            transport=httpx.MockTransport(transport),
        )

    import app.modules.ingestion.integrations.web as web_integration

    monkeypatch.setattr(web_integration, "crawl_and_extract", patched_crawl)

    with SessionLocal() as session:
        prepared = enqueue_url_source(
            session,
            kb_id=kb_ctx["kb_id"],
            user_id=kb_ctx["user_id"],
            url="https://example.com/docs",
            title="Example docs",
            max_pages=1,
            depth=0,
        )
        job_id = prepared.job_id
        document = prepared.document

    assert document.source_type == "web"
    assert document.source_uri == "https://example.com:443/docs"
    assert job_id is not None

    _execute(job_id)
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        assert job is not None
        run = session.get(ProcessingRun, job.payload["processing_run_id"])
        assert run is not None
        version_id = job.payload["document_version_id"]
        trace_id = run.trace_id

    assert _block_count(version_id) == 2
    events = _events(trace_id)
    assert events[0].stage == "extract" and events[0].status == "succeeded"
    assert events[0].output["pages"][0]["block_count"] == 2
    assert events[2].stage == "chunk" and events[2].status == "succeeded"
    assert [(event.stage, event.status, event.skip_reason) for event in events[3:]] == list(
        INDEX_STAGE_TAIL
    )