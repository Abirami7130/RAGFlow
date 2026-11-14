"""In-memory lexical posting drafts (tokenizer output; no database writes)."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass

from app.modules.chunking.tokenizer import tokenize


@dataclass(frozen=True, slots=True)
class ChunkLexicalInput:
    chunk_id: uuid.UUID
    text: str


@dataclass(frozen=True, slots=True)
class LexicalPostingDraft:
    term: str
    chunk_id: uuid.UUID
    term_frequency: int
    document_length: int
    token_count: int


def build_lexical_posting_drafts(chunks: list[ChunkLexicalInput]) -> tuple[LexicalPostingDraft, ...]:
    """Build deterministic posting drafts for each chunk. Empty token sets emit no rows."""
    drafts: list[LexicalPostingDraft] = []
    for chunk in sorted(chunks, key=lambda item: str(item.chunk_id)):
        tokens = tokenize(chunk.text)
        token_count = len(tokens)
        if token_count == 0:
            continue
        document_length = token_count
        counts = Counter(tokens)
        for term in sorted(counts):
            drafts.append(
                LexicalPostingDraft(
                    term=term,
                    chunk_id=chunk.chunk_id,
                    term_frequency=counts[term],
                    document_length=document_length,
                    token_count=token_count,
                )
            )
    return tuple(drafts)