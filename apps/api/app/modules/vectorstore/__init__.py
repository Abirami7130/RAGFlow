"""Vector index metadata and staged id allocation."""

from app.modules.vectorstore.allocation import (
    StagedVectorMappings,
    get_or_create_vector_index,
    stage_vector_mappings_for_run,
)
from app.modules.vectorstore.faiss_index import FaissIdMapIndex
from app.modules.vectorstore.live_publish import (
    PublishLiveFaissResult,
    committed_vector_id_set,
    filter_committed_search_hits,
    load_searchable_live_index,
    lookup_committed_mapping,
    publish_live_faiss_for_run,
    read_generation_stamp,
    rebuild_live_index_from_committed,
    recover_vector_index,
    run_vector_index_recovery,
    vector_index_needs_recovery,
    write_generation_stamp_atomic,
)
from app.modules.vectorstore.staged_embeddings import (
    StagedEmbeddingArtifacts,
    build_staged_embedding_artifacts_for_run,
)
from app.modules.vectorstore.staged_faiss_run import StagedFaissArtifacts, build_staged_faiss_artifacts_for_run
from app.modules.vectorstore.staged_faiss import (
    build_staged_faiss_from_sidecar,
    persist_staged_faiss_for_run,
    round_trip_sidecar_search,
    staged_faiss_is_complete,
    validate_staged_faiss_file,
    write_faiss_atomic,
)
from app.modules.vectorstore.sidecar import (
    SidecarPayload,
    load_sidecar,
    persist_run_sidecar,
    sidecar_is_complete,
    staged_vector_ids_for_run,
    validate_sidecar_arrays,
    write_sidecar_atomic,
)

__all__ = [
    "FaissIdMapIndex",
    "PublishLiveFaissResult",
    "StagedEmbeddingArtifacts",
    "StagedFaissArtifacts",
    "StagedVectorMappings",
    "SidecarPayload",
    "build_staged_embedding_artifacts_for_run",
    "build_staged_faiss_artifacts_for_run",
    "committed_vector_id_set",
    "build_staged_faiss_from_sidecar",
    "filter_committed_search_hits",
    "get_or_create_vector_index",
    "load_searchable_live_index",
    "lookup_committed_mapping",
    "publish_live_faiss_for_run",
    "read_generation_stamp",
    "rebuild_live_index_from_committed",
    "recover_vector_index",
    "run_vector_index_recovery",
    "load_sidecar",
    "persist_run_sidecar",
    "persist_staged_faiss_for_run",
    "round_trip_sidecar_search",
    "sidecar_is_complete",
    "stage_vector_mappings_for_run",
    "staged_vector_ids_for_run",
    "staged_faiss_is_complete",
    "validate_sidecar_arrays",
    "validate_staged_faiss_file",
    "vector_index_needs_recovery",
    "write_faiss_atomic",
    "write_generation_stamp_atomic",
    "write_sidecar_atomic",
]