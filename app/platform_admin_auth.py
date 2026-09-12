"""Nominal auth for the platform admin console (/platform-admin, /api/v1/platform-admin/*).

Intentionally NOT the tenant JWT system in security.py/deps.py — this console
is for the SaaS operator, not a tenant user, and isn't meant to grow beyond a
couple of trusted people. HTTP Basic against a single username + bcrypt hash
from settings is enough for that; it also means the browser handles the
login prompt/credential caching for us with zero extra frontend code.
"""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings
from app.security import verify_password

basic_scheme = HTTPBasic()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid platform admin credentials",
        headers={"WWW-Authenticate": "Basic"},
    )


def require_platform_admin(
    credentials: HTTPBasicCredentials = Depends(basic_scheme),
) -> str:
    if not settings.platform_admin_password_hash:
        # Fails closed: an unconfigured console is a locked console, not an
        # open one.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform admin console is not configured",
        )

    # secrets.compare_digest for the username to avoid a timing side-channel;
    # verify_password (bcrypt) already does the equivalent for the password.
    username_ok = secrets.compare_digest(
        credentials.username, settings.platform_admin_username
    )
    password_ok = verify_password(
        credentials.password, settings.platform_admin_password_hash
    )
    if not (username_ok and password_ok):
        raise _unauthorized()

    return credentials.username
