"""Phase 3I in-memory lexical posting drafts."""

from __future__ import annotations

import uuid

from app.modules.chunking.tokenizer import tokenize
from app.modules.lexical import ChunkLexicalInput, build_lexical_posting_drafts


def _single(text: str) -> tuple:
    chunk_id = uuid.uuid4()
    return build_lexical_posting_drafts([ChunkLexicalInput(chunk_id=chunk_id, text=text)]), chunk_id


class TestLexicalPostingDrafts:
    def test_nfkc_casefold(self) -> None:
        drafts, chunk_id = _single("ＲＵＮＮＩＮＧ café")
        terms = {draft.term for draft in drafts}
        assert "running" in terms
        assert "café" in terms or "cafe" in terms
        assert all(draft.chunk_id == chunk_id for draft in drafts)

    def test_apostrophe_split(self) -> None:
        drafts, _ = _single("Running's trail")
        by_term = {draft.term: draft for draft in drafts}
        assert "running" in by_term
        assert "s" in by_term
        assert "trail" in by_term
        assert tokenize("Running's trail") == ["running", "s", "trail"]

    def test_digit_retention(self) -> None:
        drafts, _ = _single("room 42")
        assert any(draft.term == "42" for draft in drafts)

    def test_no_stemming(self) -> None:
        drafts, _ = _single("running runner")
        terms = {draft.term for draft in drafts}
        assert "running" in terms
        assert "runner" in terms
        assert "run" not in terms

    def test_repeated_terms_aggregate_frequency(self) -> None:
        drafts, chunk_id = _single("repeat repeat once")
        by_term = {draft.term: draft for draft in drafts}
        assert by_term["repeat"].term_frequency == 2
        assert by_term["once"].term_frequency == 1
        assert by_term["repeat"].document_length == 3
        assert by_term["repeat"].token_count == 3
        assert by_term["repeat"].chunk_id == chunk_id

    def test_empty_chunk_emits_no_postings(self) -> None:
        drafts = build_lexical_posting_drafts([ChunkLexicalInput(chunk_id=uuid.uuid4(), text="  \t\n ")])
        assert drafts == ()

    def test_deterministic_ordering(self) -> None:
        chunk_a = uuid.UUID("00000000-0000-4000-8000-000000000001")
        chunk_b = uuid.UUID("00000000-0000-4000-8000-000000000002")
        first = build_lexical_posting_drafts(
            [
                ChunkLexicalInput(chunk_id=chunk_b, text="beta alpha"),
                ChunkLexicalInput(chunk_id=chunk_a, text="alpha beta"),
            ]
        )
        second = build_lexical_posting_drafts(
            [
                ChunkLexicalInput(chunk_id=chunk_a, text="alpha beta"),
                ChunkLexicalInput(chunk_id=chunk_b, text="beta alpha"),
            ]
        )
        assert first == second
        assert [draft.term for draft in first] == ["alpha", "beta", "alpha", "beta"]