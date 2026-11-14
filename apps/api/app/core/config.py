from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Rejected at startup and in Settings validation. Keep aligned with `.env.example` guidance.
INSECURE_JWT_SECRETS = frozenset(
    {
        "dev-only-change-me",
        "replace-with-a-long-random-string",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://ragflow:ragflow_dev_password@localhost:5432/ragflow"
    )
    jwt_secret: str
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 1_209_600
    cors_origins: str = "http://localhost:5173"
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_name: str = "Admin"
    log_level: str = "INFO"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_revision: str = ""
    embedding_use_fake: bool = False
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    gemini_model: str = ""
    gemini_api_key: str = ""
    storage_dir: str = Field(default="storage")
    index_dir: str = Field(default="indexes")
    max_upload_bytes: int = Field(default=26_214_400, description="25 MiB")

    @field_validator("max_upload_bytes")
    @classmethod
    def upload_limit_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("MAX_UPLOAD_BYTES must be positive.")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_must_be_configured(cls, value: str) -> str:
        secret = value.strip()
        if not secret:
            raise ValueError("JWT_SECRET is required.")
        if secret in INSECURE_JWT_SECRETS:
            raise ValueError("JWT_SECRET must not use a development placeholder.")
        if len(secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters.")
        return secret

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
