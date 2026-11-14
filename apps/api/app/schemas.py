from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_serializer


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=80)


class UserRead(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RetrievalConfig(BaseModel):
    top_k_vector: int = Field(default=20, ge=1, le=100)
    top_k_lexical: int = Field(default=20, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=200)
    rerank_top_n: int = Field(default=20, ge=1, le=100)
    context_k: int = Field(default=5, ge=1, le=20)
    context_token_budget: int = Field(default=3000, ge=200, le=16000)
    hybrid_enabled: bool = True
    rerank_enabled: bool = True
    bm25_k1: float = Field(default=1.5, gt=0, le=3)
    bm25_b: float = Field(default=0.75, ge=0, le=1)


class ChunkConfig(BaseModel):
    strategy: str = "structure_aware"
    target_tokens: int = Field(default=400, ge=50, le=2000)
    overlap_tokens: int = Field(default=60, ge=0, le=400)
    split_oversized_blocks: bool = True


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    retrieval_config: RetrievalConfig | None = None
    chunk_config: ChunkConfig | None = None


class KnowledgeBaseRead(BaseModel):
    id: UUID
    name: str
    description: str
    role: str
    document_count: int
    ready_count: int
    retrieval_config: RetrievalConfig
    chunk_config: ChunkConfig
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_timestamps(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class KnowledgeBasePage(BaseModel):
    items: list[KnowledgeBaseRead]
    next_cursor: str | None


class KnowledgeBaseDeleted(BaseModel):
    id: UUID
    deleted: bool = True


class MemberWrite(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(owner|editor|viewer)$")


class MemberRead(BaseModel):
    user_id: UUID
    email: EmailStr
    display_name: str
    role: str


class MemberList(BaseModel):
    items: list[MemberRead]


class ProviderStatus(BaseModel):
    model: str
    ready: bool
    provider: str | None = None


class ProvidersRead(BaseModel):
    embedding: ProviderStatus
    reranker: ProviderStatus
    language_model: ProviderStatus


class HealthRead(BaseModel):
    status: str
    database: str


class DocumentRead(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    title: str
    source_type: str
    source_uri: str | None
    status: str
    error_code: str | None
    error_detail: str | None
    current_version_id: UUID | None
    current_version_number: int | None
    preview_version_id: UUID | None
    pending_version_id: UUID | None
    pending_run_id: UUID | None
    pending_status: str | None
    chunk_preview_run_id: UUID | None
    byte_size: int | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_timestamps(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class DocumentUploadResult(BaseModel):
    document: DocumentRead
    reused: bool = False
    job_id: UUID | None = None


class DocumentDeleteResult(BaseModel):
    job_id: UUID


class UrlSourceRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    max_pages: int = Field(default=20, ge=1, le=20)
    depth: int = Field(default=1, ge=0, le=1)


class ReprocessResult(BaseModel):
    processing_run_id: UUID
    trace_id: UUID
    job_id: UUID


class CanonicalBlockRead(BaseModel):
    id: UUID
    sequence: int
    block_type: str
    text: str
    locator: dict
    metadata: dict


class CanonicalBlockPage(BaseModel):
    items: list[CanonicalBlockRead]
    next_cursor: str | None


class ChunkRead(BaseModel):
    id: UUID
    sequence: int
    text: str
    token_count: int
    block_start_id: UUID
    block_end_id: UUID
    locator_start: dict
    locator_end: dict
    metadata: dict
    embedding: None = None


class ChunkPage(BaseModel):
    items: list[ChunkRead]
    next_cursor: str | None


class ProcessingRunRead(BaseModel):
    id: UUID
    document_version_id: UUID
    status: str
    is_active: bool
    superseded_at: datetime | None
    retired_at: datetime | None
    chunk_strategy: dict
    embedding_model: str | None
    embedding_revision: str | None
    embedding_dimension: int | None
    chunk_count: int
    trace_id: UUID | None
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None

    @field_serializer("started_at", "finished_at", "superseded_at", "retired_at")
    def serialize_optional_timestamps(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
