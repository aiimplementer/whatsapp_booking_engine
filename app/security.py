import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum

import jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class TokenPayload(BaseModel):
    sub: str          # tenant_user id
    tenant_id: str
    role: str
    type: TokenType
    exp: datetime
    iat: datetime


def _create_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str,
    token_type: TokenType, expires_delta: timedelta,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
    return _create_token(
        user_id=user_id, tenant_id=tenant_id, role=role,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
    return _create_token(
        user_id=user_id, tenant_id=tenant_id, role=role,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )


class InvalidTokenError(Exception):
    pass


def decode_token(token: str, *, expected_type: TokenType) -> TokenPayload:
    """
    Raises InvalidTokenError for any failure (expired, bad signature, wrong
    type). Deliberately one exception type for all failure modes — callers
    (the FastAPI dependency) always respond 401 either way, and collapsing
    the cases here avoids a caller accidentally handling "expired" differently
    from "forged" and leaking which one it was to the client.
    """
    try:
        raw = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    if raw.get("type") != expected_type.value:
        raise InvalidTokenError(f"expected token type {expected_type.value!r}")

    return TokenPayload(**raw)
