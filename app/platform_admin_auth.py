"""Auth for the platform admin console (/platform-admin, /api/v1/platform-admin/*).

Intentionally NOT the tenant JWT system in security.py/deps.py — this console
is for the SaaS operator, not a tenant user, and there's no tenant_users row
to hang a "sub" off of. It's still just one username + one bcrypt hash from
settings (this console isn't meant to grow beyond a couple of trusted
people), but credentials are exchanged once at /login for a short-lived JWT
rather than sent on every request via HTTP Basic. That gets us a real login
page instead of the browser's native Basic-auth popup, and a token the
frontend can hold in localStorage exactly like the tenant admin app does.
"""
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.security import verify_password

bearer_scheme = HTTPBearer(auto_error=False)

TOKEN_TYPE = "platform_admin"


def _unauthorized(detail: str = "Invalid or expired session") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def verify_platform_admin_credentials(username: str, password: str) -> bool:
    """Checks a username/password pair against the configured operator account.

    Raises 503 (fails closed) if the console has no password hash configured
    at all — an unconfigured console is a locked console, not an open one.
    """
    if not settings.platform_admin_password_hash:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform admin console is not configured",
        )

    # secrets.compare_digest for the username to avoid a timing side-channel;
    # verify_password (bcrypt) already does the equivalent for the password.
    username_ok = secrets.compare_digest(username, settings.platform_admin_username)
    password_ok = verify_password(password, settings.platform_admin_password_hash)
    return username_ok and password_ok


def create_platform_admin_token(username: str) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": username,
        "type": TOKEN_TYPE,
        "iat": now,
        "exp": now + expires_delta,
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


def require_platform_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """FastAPI dependency: validates the Bearer token, returns the operator username."""
    if credentials is None:
        raise _unauthorized("Missing platform admin session")

    try:
        raw = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError:
        raise _unauthorized() from None

    if raw.get("type") != TOKEN_TYPE or "sub" not in raw:
        raise _unauthorized()

    return raw["sub"]
