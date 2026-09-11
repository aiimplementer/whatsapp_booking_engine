import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_db_for_tenant
from app.models.tenant import Tenant, TenantUser
from app.security import InvalidTokenError, TokenPayload, TokenType, decode_token

bearer_scheme = HTTPBearer()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_token_payload(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> TokenPayload:
    try:
        return decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
    except InvalidTokenError as exc:
        raise _unauthorized(str(exc)) from exc


async def get_tenant_db(
    payload: TokenPayload = Depends(get_current_token_payload),
) -> AsyncGenerator[AsyncSession, None]:
    """
    Tenant-scoped, RLS-enforced session derived from the verified access
    token — NOT from a client-supplied header. The token's tenant_id claim
    was set by our own /auth/login endpoint, so there's no way for a caller
    to request one tenant's token and read another tenant's data by just
    changing a header value.
    """
    async for session in get_db_for_tenant(payload.tenant_id):
        yield session


async def get_current_user(
    payload: TokenPayload = Depends(get_current_token_payload),
    db: AsyncSession = Depends(get_tenant_db),
) -> TenantUser:
    """
    Re-fetches the user from the DB on every request rather than trusting the
    JWT's role claim blindly. This means a role change or account disable
    takes effect on the user's very next request instead of waiting up to
    access_token_expire_minutes for the old token to expire.
    """
    result = await db.execute(
        select(TenantUser).where(TenantUser.id == uuid.UUID(payload.sub))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise _unauthorized("User no longer exists")
    return user


def require_role(*allowed_roles: str):
    """Usage: Depends(require_role("admin")) or Depends(require_role("admin", "staff"))"""

    async def checker(user: TenantUser = Depends(get_current_user)) -> TenantUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {' or '.join(allowed_roles)}",
            )
        return user

    return checker


# ----------------------------------------------------------------------------
# Public (unauthenticated) booking endpoints resolve their tenant from a
# :tenant_slug path parameter instead of a JWT. This is intentionally the
# ONLY place that's true — see get_tenant_db's docstring above for why every
# other endpoint must go through the verified-token path instead.
# ----------------------------------------------------------------------------

async def resolve_tenant_by_slug(
    tenant_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.slug == tenant_slug, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalar_one_or_none()
    if tenant is None or tenant.status == "suspended":
        # Same 404 either way — don't let a caller distinguish "no such
        # business" from "business exists but is suspended".
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")
    return tenant


async def get_public_tenant_db(
    tenant: Tenant = Depends(resolve_tenant_by_slug),
) -> AsyncGenerator[AsyncSession, None]:
    """RLS-scoped session for the tenant identified by the URL slug — the
    public-booking equivalent of get_tenant_db, just keyed off the slug
    instead of a JWT claim since there's no logged-in user here."""
    async for session in get_db_for_tenant(str(tenant.id)):
        yield session
