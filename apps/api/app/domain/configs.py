"""Default knowledge-base configuration stored for later phases.

Phase 1 persists these objects and lets editors edit them.
It does not execute retrieval or chunking.
"""

DEFAULT_RETRIEVAL_CONFIG: dict = {
    "top_k_vector": 20,
    "top_k_lexical": 20,
    "rrf_k": 60,
    "rerank_top_n": 20,
    "context_k": 5,
    "context_token_budget": 3000,
    "hybrid_enabled": True,
    "rerank_enabled": True,
    "bm25_k1": 1.5,
    "bm25_b": 0.75,
}

DEFAULT_CHUNK_CONFIG: dict = {
    "strategy": "structure_aware",
    "target_tokens": 400,
    "overlap_tokens": 60,
    "split_oversized_blocks": True,
}
