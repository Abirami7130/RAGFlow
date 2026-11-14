"""Okapi BM25 lexical scoring (query against one chunk)."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.configs import DEFAULT_RETRIEVAL_CONFIG
from app.models import ChunkStats, LexicalPosting
from app.modules.chunking.tokenizer import tokenize
from app.modules.lexical.corpus import load_lexical_corpus_snapshot

if TYPE_CHECKING:
    from app.modules.lexical.corpus import LexicalCorpusSnapshot

BM25_K1: float = float(DEFAULT_RETRIEVAL_CONFIG["bm25_k1"])
BM25_B: float = float(DEFAULT_RETRIEVAL_CONFIG["bm25_b"])


@dataclass(frozen=True, slots=True)
class Bm25CorpusParams:
    chunk_count: int
    avg_token_length: float
    document_frequencies: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class Bm25ChunkParams:
    document_length: int
    term_frequencies: Mapping[str, int]


def bm25_idf(*, chunk_count: int, document_frequency: int) -> float:
    """BM25 idf with the architecture's smoothed formula."""
    if chunk_count <= 0 or document_frequency <= 0:
        return 0.0
    return math.log(1.0 + (chunk_count - document_frequency + 0.5) / (document_frequency + 0.5))


def bm25_term_contribution(
    *,
    term_frequency: int,
    document_length: int,
    avg_token_length: float,
    idf: float,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> float:
    if term_frequency <= 0 or idf <= 0.0 or avg_token_length <= 0.0:
        return 0.0
    length_norm = 1.0 - b + b * (document_length / avg_token_length)
    return idf * (term_frequency * (k1 + 1.0)) / (term_frequency + k1 * length_norm)


def bm25_score(
    query: str,
    *,
    corpus: Bm25CorpusParams,
    chunk: Bm25ChunkParams,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> float:
    """Score one chunk for a query string. Query terms use the lexical tokenizer."""
    if corpus.chunk_count <= 0 or corpus.avg_token_length <= 0.0:
        return 0.0
    total = 0.0
    for term in tokenize(query):
        document_frequency = int(corpus.document_frequencies.get(term, 0))
        idf = bm25_idf(chunk_count=corpus.chunk_count, document_frequency=document_frequency)
        term_frequency = int(chunk.term_frequencies.get(term, 0))
        total += bm25_term_contribution(
            term_frequency=term_frequency,
            document_length=chunk.document_length,
            avg_token_length=corpus.avg_token_length,
            idf=idf,
            k1=k1,
            b=b,
        )
    return total


def corpus_params_from_snapshot(snapshot: LexicalCorpusSnapshot) -> Bm25CorpusParams:
    return Bm25CorpusParams(
        chunk_count=snapshot.chunk_count,
        avg_token_length=snapshot.avg_token_length,
        document_frequencies=dict(snapshot.term_document_frequencies),
    )


def bm25_score_chunk_from_db(
    db: Session,
    *,
    knowledge_base_id: uuid.UUID,
    chunk_id: uuid.UUID,
    query: str,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> float:
    snapshot = load_lexical_corpus_snapshot(db, knowledge_base_id)
    if snapshot is None:
        return 0.0
    stats = db.get(ChunkStats, chunk_id)
    if stats is None:
        raise ValueError(f"chunk_stats missing for chunk {chunk_id}")
    postings = list(
        db.scalars(
            select(LexicalPosting).where(
                LexicalPosting.knowledge_base_id == knowledge_base_id,
                LexicalPosting.chunk_id == chunk_id,
            )
        ).all()
    )
    term_frequencies = {row.term: row.term_frequency for row in postings}
    return bm25_score(
        query,
        corpus=corpus_params_from_snapshot(snapshot),
        chunk=Bm25ChunkParams(document_length=stats.token_count, term_frequencies=term_frequencies),
        k1=k1,
        b=b,
    )