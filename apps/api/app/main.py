import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Response
from pydantic import ValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, database_status
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.modules.auth.router import router as auth_router
from app.modules.auth.service import bootstrap_admin
from app.modules.ingestion.recovery import run_storage_recovery
from app.modules.vectorstore.live_publish import run_vector_index_recovery
from app.modules.chunking.router import router as chunking_router
from app.modules.ingestion.router import router as documents_router
from app.modules.knowledge_bases.router import router as knowledge_router
from app.modules.settings.router import router as settings_router
from app.schemas import HealthRead

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    db: Session = SessionLocal()
    try:
        bootstrap_admin(db)
        stats = run_storage_recovery(db)
        if any(stats.values()):
            logger.info("storage_recovery %s", stats)
        index_stats = run_vector_index_recovery(db)
        if any(index_stats.values()):
            logger.info("vector_index_recovery %s", index_stats)
        db.commit()
    finally:
        db.close()
    logger.info("api_started")
    yield


def create_app() -> FastAPI:
    try:
        settings = get_settings()
    except ValidationError as exc:
        raise SystemExit(f"Invalid configuration. Set JWT_SECRET in .env: {exc}") from exc
    app = FastAPI(title="RAGFlow", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)

    health = APIRouter()

    @health.get("/health", response_model=HealthRead)
    def health_check(response: Response) -> HealthRead:
        database = database_status()
        if database != "ok":
            response.status_code = 503
            return HealthRead(status="degraded", database=database)
        return HealthRead(status="ok", database="ok")

    api = APIRouter(prefix="/api/v1")
    api.include_router(health)
    api.include_router(auth_router)
    api.include_router(knowledge_router)
    api.include_router(documents_router)
    api.include_router(chunking_router)
    api.include_router(settings_router)
    app.include_router(api)
    return app


app = create_app()
