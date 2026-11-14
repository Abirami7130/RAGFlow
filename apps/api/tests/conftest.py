import os
from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-characters")
os.environ.setdefault("EMBEDDING_USE_FAKE", "1")
os.environ.setdefault("INDEX_DIR", "indexes")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "")
os.environ.setdefault("LOG_LEVEL", "WARNING")


def _default_test_database_url() -> str:
    """Prefer explicit DATABASE_URL, else local Postgres via PGUSER or the login name."""
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    user = os.environ.get("PGUSER") or os.environ.get("USER") or ""
    if user:
        return f"postgresql+psycopg://{user}@localhost:5432/ragflow_test"
    return "postgresql+psycopg://localhost:5432/ragflow_test"


def _ensure_database(url: str) -> None:
    target = make_url(url)
    database = target.database
    admin = target.set(database="postgres")
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        engine.dispose()


def _resolve_test_database_url() -> str:
    url = _default_test_database_url()
    if url.endswith("/ragflow"):
        url = url[: -len("/ragflow")] + "/ragflow_test"
    return url


_TEST_DATABASE_URL = _resolve_test_database_url()
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL

from app.core.config import get_settings  # noqa: E402
from app.core.db import rebind  # noqa: E402

get_settings.cache_clear()
_ensure_database(_TEST_DATABASE_URL)
rebind(_TEST_DATABASE_URL)
command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(scope="session")
def database_url() -> str:
    return _TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def clean_tables(database_url: str) -> Generator[None, None, None]:
    from app.core.db import engine

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                  chunks,
                  trace_events,
                  jobs,
                  canonical_blocks,
                  processing_runs,
                  query_traces,
                  document_versions,
                  documents,
                  knowledge_base_members,
                  knowledge_bases,
                  refresh_tokens,
                  users
                RESTART IDENTITY CASCADE
                """
            )
        )
    yield


@pytest.fixture(autouse=True)
def isolated_storage_autouse(isolated_storage: str) -> str:
    return isolated_storage


@pytest.fixture(autouse=True)
def isolated_index_dir(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    import shutil
    import tempfile

    from app.core.config import get_settings

    path = tempfile.mkdtemp(prefix="ragflow-indexes-")
    monkeypatch.setenv("INDEX_DIR", path)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def isolated_storage(monkeypatch: pytest.MonkeyPatch) -> str:
    import shutil
    import tempfile

    from app.core.config import get_settings

    path = tempfile.mkdtemp(prefix="ragflow-storage-")
    monkeypatch.setenv("STORAGE_DIR", path)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def client(database_url: str) -> Generator[TestClient, None, None]:
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
