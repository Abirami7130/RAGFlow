from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.models import User
from app.modules.auth.router import get_current_user
from app.schemas import ProviderStatus, ProvidersRead

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/providers", response_model=ProvidersRead)
def providers(_user: User = Depends(get_current_user)) -> ProvidersRead:
    """Report configuration only. Phase 1 does not load embedding or language models."""
    settings = get_settings()
    return ProvidersRead(
        embedding=ProviderStatus(model=settings.embedding_model, ready=False),
        reranker=ProviderStatus(model=settings.rerank_model, ready=False),
        language_model=ProviderStatus(
            provider="gemini",
            model=settings.gemini_model or "unset",
            ready=False,
        ),
    )
