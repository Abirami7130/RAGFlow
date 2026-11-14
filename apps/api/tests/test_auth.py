from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import hash_password, verify_password
from app.models import RefreshToken, User


def test_password_hash_is_not_the_password() -> None:
    raw = "correct-horse-battery"
    hashed = hash_password(raw)
    assert hashed != raw
    assert hashed.startswith("$argon2")
    assert verify_password(raw, hashed)
    assert not verify_password("incorrect-horse", hashed)


def test_health_reports_database(client) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_register_login_me_and_refresh(client) -> None:
    created = client.post(
        "/api/v1/auth/register",
        json={"email": "Ada@Example.com", "password": "a-long-password", "display_name": "Ada"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "ada@example.com"
    assert "password" not in body

    duplicate = client.post(
        "/api/v1/auth/register",
        json={"email": "ada@example.com", "password": "a-long-password", "display_name": "Ada"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    short = client.post(
        "/api/v1/auth/register",
        json={"email": "short@example.com", "password": "short", "display_name": "Short"},
    )
    assert short.status_code == 400
    assert short.json()["error"]["code"] == "validation_error"

    bad = client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong-password"})
    assert bad.status_code == 401

    logged = client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "a-long-password"})
    assert logged.status_code == 200
    tokens = logged.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 900

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["display_name"] == "Ada"

    missing = client.get("/api/v1/auth/me")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "unauthenticated"

    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    next_tokens = refreshed.json()
    assert next_tokens["refresh_token"] != tokens["refresh_token"]

    reused = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reused.status_code == 401
    revoked = client.post("/api/v1/auth/refresh", json={"refresh_token": next_tokens["refresh_token"]})
    assert revoked.status_code == 401


def test_logout_revokes_refresh_token(client) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"email": "bye@example.com", "password": "a-long-password", "display_name": "Bye"},
    )
    tokens = client.post(
        "/api/v1/auth/login", json={"email": "bye@example.com", "password": "a-long-password"}
    ).json()
    gone = client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert gone.status_code == 204
    again = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == 401


def test_expired_access_token_is_rejected(client) -> None:
    created = client.post(
        "/api/v1/auth/register",
        json={"email": "expired@example.com", "password": "a-long-password", "display_name": "Expired"},
    )
    user_id = UUID(created.json()["id"])
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": str(user_id),
            "typ": "access",
            "exp": datetime.now(UTC) - timedelta(seconds=30),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_malformed_refresh_token_is_rejected(client) -> None:
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "not-a-valid-stored-token-value-0123456789"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_concurrent_refresh_issues_one_successor(client) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"email": "race@example.com", "password": "a-long-password", "display_name": "Race"},
    )
    refresh_token = client.post(
        "/api/v1/auth/login",
        json={"email": "race@example.com", "password": "a-long-password"},
    ).json()["refresh_token"]

    def rotate() -> int:
        return client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token}).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = [future.result() for future in as_completed(pool.submit(rotate) for _ in range(4))]

    assert codes.count(200) == 1
    assert codes.count(401) == 3

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "race@example.com"))
        assert user is not None
        issued = db.scalar(
            select(func.count()).select_from(RefreshToken).where(RefreshToken.user_id == user.id)
        )
        assert issued == 2


def test_stored_password_is_hashed(client) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"email": "hash@example.com", "password": "a-long-password", "display_name": "Hash"},
    )
    from sqlalchemy import text

    from app.core.db import engine

    with engine.connect() as connection:
        stored = connection.execute(text("SELECT password_hash FROM users WHERE email = 'hash@example.com'")).scalar()
    assert stored != "a-long-password"
    assert stored.startswith("$argon2")
