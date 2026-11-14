import pytest
from pydantic import ValidationError

from app.core.config import INSECURE_JWT_SECRETS, Settings, get_settings


def test_jwt_secret_missing(monkeypatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert any(err["loc"] == ("jwt_secret",) for err in exc.value.errors())


def test_jwt_secret_rejects_placeholders(monkeypatch) -> None:
    for placeholder in INSECURE_JWT_SECRETS:
        monkeypatch.setenv("JWT_SECRET", placeholder)
        with pytest.raises(ValidationError) as exc:
            Settings()
        messages = " ".join(err["msg"] for err in exc.value.errors())
        assert "placeholder" in messages.lower() or "required" in messages.lower()


def test_jwt_secret_rejects_short_values(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(ValidationError) as exc:
        Settings()
    messages = " ".join(err["msg"] for err in exc.value.errors())
    assert "32" in messages


def test_create_app_starts_with_valid_secret(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-at-least-32-characters")
    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    assert app.title == "RAGFlow"


def test_create_app_rejects_placeholder_secret(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "replace-with-a-long-random-string")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        get_settings()
    get_settings.cache_clear()
