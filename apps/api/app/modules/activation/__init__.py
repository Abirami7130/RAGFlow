"""Document activation, rollback, retirement, and purge."""

from app.modules.activation.active_chunks import active_chunk_ids_for_knowledge_base
from app.modules.activation.delete import enqueue_purge_document, soft_delete_document
from app.modules.activation.purge import purge_document
from app.modules.activation.readiness import run_ready_for_activation
from app.modules.activation.retire import enqueue_retire_runs, plan_retirements_after_activation, retire_processing_run
from app.modules.activation.retrieval import vector_mapping_visible_in_search
from app.modules.activation.service import ActivationResult, activate_processing_run, rollback_document_serving
from app.modules.activation.stage import complete_activate_stage

__all__ = [
    "ActivationResult",
    "activate_processing_run",
    "active_chunk_ids_for_knowledge_base",
    "complete_activate_stage",
    "enqueue_purge_document",
    "enqueue_retire_runs",
    "plan_retirements_after_activation",
    "purge_document",
    "retire_processing_run",
    "rollback_document_serving",
    "run_ready_for_activation",
    "soft_delete_document",
    "vector_mapping_visible_in_search",
]