import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import APIError
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    verify_password,
)
from app.models import RefreshToken, User
from app.schemas import TokenPair, UserCreate, UserRead

logger = logging.getLogger(__name__)


def to_user_read(user: User) -> UserRead:
    return UserRead(id=user.id, email=user.email, display_name=user.display_name)


def register_user(db: Session, payload: UserCreate) -> UserRead:
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None:
        raise APIError(409, "conflict", "An account with that email already exists.")
    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("user_registered user_id=%s", user.id)
    return to_user_read(user)


def _issue_tokens(db: Session, user: User) -> TokenPair:
    settings = get_settings()
    access, ttl = create_access_token(user.id)
    secret = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(secret),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.jwt_refresh_ttl_seconds),
        )
    )
    db.commit()
    return TokenPair(access_token=access, refresh_token=secret, expires_in=ttl)


def login(db: Session, email: str, password: str) -> TokenPair:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None or not verify_password(password, user.password_hash):
        raise APIError(401, "unauthenticated", "Email or password is incorrect.")
    return _issue_tokens(db, user)


def refresh(db: Session, refresh_token: str) -> TokenPair:
    token_hash = hash_token(refresh_token)
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update())
    if row is None:
        raise APIError(401, "unauthenticated", "Refresh token is invalid.")
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        # Reuse of a revoked token revokes the rest of this user's live tokens.
        live = db.scalars(
            select(RefreshToken).where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
        ).all()
        for item in live:
            item.revoked_at = now
        db.commit()
        logger.warning("refresh_reuse user_id=%s", row.user_id)
        raise APIError(401, "unauthenticated", "Refresh token is invalid.")
    if row.expires_at <= now:
        raise APIError(401, "unauthenticated", "Refresh token is invalid.")
    user = db.get(User, row.user_id)
    if user is None:
        row.revoked_at = now
        db.commit()
        raise APIError(401, "unauthenticated", "Refresh token is invalid.")
    row.revoked_at = now
    settings = get_settings()
    access, ttl = create_access_token(user.id)
    secret = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(secret),
            expires_at=now + timedelta(seconds=settings.jwt_refresh_ttl_seconds),
        )
    )
    db.commit()
    return TokenPair(access_token=access, refresh_token=secret, expires_in=ttl)


def logout(db: Session, refresh_token: str) -> None:
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.commit()


def bootstrap_admin(db: Session) -> None:
    settings = get_settings()
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return
    existing = db.scalar(select(User.id).limit(1))
    if existing is not None:
        return
    if len(settings.bootstrap_admin_password) < 10:
        logger.error("bootstrap_admin_skipped reason=password_too_short")
        return
    user = User(
        email=settings.bootstrap_admin_email.lower(),
        password_hash=hash_password(settings.bootstrap_admin_password),
        display_name=settings.bootstrap_admin_name.strip() or "Admin",
    )
    db.add(user)
    db.commit()
    logger.info("bootstrap_admin_created user_id=%s", user.id)
