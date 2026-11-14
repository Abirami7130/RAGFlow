"""Phase 3A structure-aware chunking."""

from __future__ import annotations

import uuid

import pytest

from app.domain.configs import DEFAULT_CHUNK_CONFIG
from app.domain.extraction import NormalizedBlock
from app.modules.chunking.service import build_chunks_for_blocks
from app.modules.chunking.structure import _BlockInput, build_structure_aware_chunks
from app.modules.chunking.tokenizer import tokenize
from app.models import CanonicalBlock


class TestTokenizer:
    def test_nfkc_casefold_and_apostrophe_split(self) -> None:
        tokens = tokenize("Running's trail — 42")
        assert "running" in tokens
        assert "s" in tokens
        assert "trail" in tokens
        assert "42" in tokens

    def test_no_stemming(self) -> None:
        assert "running" in tokenize("running")


class TestStructureAwareChunker:
    def test_whole_blocks_concatenate_within_budget(self) -> None:
        blocks = [
            _BlockInput("paragraph", "alpha", {"source_type": "txt", "heading_path": []}),
            _BlockInput("paragraph", "beta", {"source_type": "txt", "heading_path": []}),
        ]
        chunks = build_structure_aware_chunks(
            blocks,
            target_tokens=10,
            overlap_tokens=0,
            split_oversized_blocks=True,
        )
        assert len(chunks) == 1
        assert chunks[0].block_start_index == 0
        assert chunks[0].block_end_index == 1
        assert "alpha\n\nbeta" in chunks[0].text

    def test_heading_starts_new_chunk(self) -> None:
        blocks = [
            _BlockInput("paragraph", "intro", {"source_type": "md", "heading_path": ["Root"]}),
            _BlockInput("heading", "Section", {"source_type": "md", "heading_path": ["Root", "Section"]}),
            _BlockInput("paragraph", "body", {"source_type": "md", "heading_path": ["Root", "Section"]}),
        ]
        chunks = build_structure_aware_chunks(
            blocks,
            target_tokens=50,
            overlap_tokens=0,
            split_oversized_blocks=True,
        )
        assert len(chunks) == 2
        assert chunks[0].text.startswith("Root\n\nintro")
        assert chunks[0].metadata.get("heading_prefix") is True
        assert chunks[1].text == "Section\n\nbody"
        assert chunks[1].metadata.get("heading_prefix") is not True

    def test_oversized_block_splits_with_overlap(self) -> None:
        words = " ".join(f"w{index}" for index in range(120))
        blocks = [_BlockInput("paragraph", words, {"source_type": "txt", "heading_path": []})]
        chunks = build_structure_aware_chunks(
            blocks,
            target_tokens=20,
            overlap_tokens=5,
            split_oversized_blocks=True,
        )
        assert len(chunks) > 1
        assert all(chunk.block_start_index == chunk.block_end_index == 0 for chunk in chunks)
        assert all(chunk.metadata.get("block_split") for chunk in chunks)
        assert chunks[0].locator_start["char_start"] == 0
        assert chunks[0].locator_end["char_end"] is not None


def _as_canonical(blocks: list[NormalizedBlock]) -> list[CanonicalBlock]:
    rows: list[CanonicalBlock] = []
    for block in blocks:
        rows.append(
            CanonicalBlock(
                id=uuid.uuid4(),
                document_version_id=uuid.uuid4(),
                sequence=block.sequence,
                block_type=block.block_type,
                text=block.text,
                locator=block.locator,
                block_metadata=block.metadata,
            )
        )
    return rows


@pytest.mark.parametrize(
    ("fixture", "source_type"),
    [
        ("sample.txt", "txt"),
        ("sample.md", "md"),
    ],
)
def test_fixture_chunk_counts_are_stable(fixture: str, source_type: str) -> None:
    from pathlib import Path

    from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks

    data = (Path(__file__).parent / "fixtures" / "extraction" / fixture).read_bytes()
    blocks = _as_canonical(normalize_blocks(extract_raw_blocks(source_type, data)))
    first = build_chunks_for_blocks(blocks, strategy=DEFAULT_CHUNK_CONFIG)
    second = build_chunks_for_blocks(blocks, strategy=DEFAULT_CHUNK_CONFIG)
    assert first
    assert [(item.sequence, item.text, item.token_count) for item in first] == [
        (item.sequence, item.text, item.token_count) for item in second
    ]
    start_ids = {blocks[item.block_start_index].id for item in first}
    end_ids = {blocks[item.block_end_index].id for item in first}
    assert start_ids.issubset({block.id for block in blocks})
    assert end_ids.issubset({block.id for block in blocks})


class TestChunkReadApi:
    def test_run_and_chunk_endpoints(self, client) -> None:
        from app.core.db import SessionLocal
        from app.models import Chunk, ProcessingRun

        from tests.test_canonical_blocks import _auth, _execute, _headers

        owner_email = f"{uuid.uuid4().hex}@example.com"
        owner = _auth(client, owner_email)
        created = client.post(
            "/api/v1/knowledge-bases",
            headers=_headers(owner),
            json={"name": "Chunk KB", "description": ""},
        )
        kb_id = created.json()["id"]
        uploaded = client.post(
            f"/api/v1/knowledge-bases/{kb_id}/documents",
            headers=_headers(owner),
            files={"file": ("notes.txt", b"Alpha\n\nBeta section\n\nMore text", "text/plain")},
        )
        assert uploaded.status_code == 201
        body = uploaded.json()
        job_id = uuid.UUID(body["job_id"])
        version_id = uuid.UUID(body["document"]["pending_version_id"])
        _execute(job_id)

        with SessionLocal() as session:
            from sqlalchemy import select

            run = session.scalar(
                select(ProcessingRun)
                .where(ProcessingRun.document_version_id == version_id)
                .order_by(ProcessingRun.created_at.desc())
                .limit(1)
            )
            assert run is not None
            run_id = run.id
            chunk_count = run.chunk_count

        run_resp = client.get(f"/api/v1/processing-runs/{run_id}", headers=_headers(owner))
        assert run_resp.status_code == 200
        payload = run_resp.json()
        assert payload["chunk_count"] == chunk_count
        assert payload["chunk_strategy"]["strategy"] == "structure_aware"

        list_resp = client.get(f"/api/v1/processing-runs/{run_id}/chunks", headers=_headers(owner))
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert len(items) == chunk_count
        assert items[0]["embedding"] is None

        single = client.get(f"/api/v1/chunks/{items[0]['id']}", headers=_headers(owner))
        assert single.status_code == 200
        assert single.json()["id"] == items[0]["id"]

        with SessionLocal() as session:
            stored = session.get(Chunk, uuid.UUID(items[0]["id"]))
            assert stored is not None
            assert stored.block_start_id is not None
            assert stored.locator_start["source_type"] == "txt"

        doc_resp = client.get(f"/api/v1/documents/{body['document']['id']}", headers=_headers(owner))
        assert doc_resp.status_code == 200
        assert doc_resp.json()["chunk_count"] == chunk_count
        assert doc_resp.json()["chunk_preview_run_id"] == str(run_id)