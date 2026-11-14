from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import APIError
from app.core.security import decode_access_token
from app.models import User
from app.modules.auth.service import login, logout, refresh, register_user, to_user_read
from app.schemas import LoginRequest, RefreshRequest, TokenPair, UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise APIError(401, "unauthenticated", "Authentication is required.")
    try:
        user_id = decode_access_token(credentials.credentials)
    except ValueError:
        raise APIError(401, "unauthenticated", "Authentication is required.") from None
    user = db.get(User, user_id)
    if user is None:
        raise APIError(401, "unauthenticated", "Authentication is required.")
    return user


@router.post("/register", response_model=UserRead, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> UserRead:
    return register_user(db, payload)


@router.post("/login", response_model=TokenPair)
def login_route(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenPair:
    return login(db, payload.email, payload.password)


@router.post("/refresh", response_model=TokenPair)
def refresh_route(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    return refresh(db, payload.refresh_token)


@router.post("/logout", status_code=204)
def logout_route(payload: RefreshRequest, db: Session = Depends(get_db)) -> None:
    logout(db, payload.refresh_token)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> UserRead:
    return to_user_read(user)


def require_user_id(user: User = Depends(get_current_user)) -> UUID:
    return user.id
