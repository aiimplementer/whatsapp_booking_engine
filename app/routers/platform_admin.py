import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_db_for_tenant
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant, TenantUser
from app.platform_admin_auth import require_platform_admin
from app.schemas.platform_admin import (
    PaginatedTenants,
    PlatformTenantOut,
    SubscriptionUpdate,
)

router = APIRouter(
    prefix="/api/v1/platform-admin",
    tags=["platform-admin"],
    dependencies=[Depends(require_platform_admin)],
)


async def _admin_email_for(tenant_id: uuid.UUID) -> str | None:
    """Looks up the primary (earliest-added) admin's email for one tenant.

    tenant_users is RLS-protected (see database.py's get_db_for_tenant
    docstring), and the platform admin console has no DB role that bypasses
    RLS — so this borrows the same per-tenant-scoped-session mechanism every
    tenant-facing request uses, one tenant at a time, rather than reaching
    for elevated DB credentials just for this one read.
    """
    async for db in get_db_for_tenant(str(tenant_id)):
        result = await db.execute(
            select(TenantUser.email)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.role == "admin")
            .order_by(TenantUser.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()
    return None


@router.get("/tenants", response_model=PaginatedTenants)
async def list_tenants(
    search: str | None = Query(default=None, description="Matches business name, email, or slug"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    # `tenants` itself has no RLS policy (only per-tenant child tables do —
    # see 0001_initial_schema.py), so a plain, tenant-agnostic session can
    # read across every tenant here with no extra bypass needed.
    filters = [Tenant.deleted_at.is_(None)]
    if search:
        like = f"%{search.strip()}%"
        filters.append(
            or_(Tenant.name.ilike(like), Tenant.email.ilike(like), Tenant.slug.ilike(like))
        )

    total = await db.scalar(
        select(func.count()).select_from(Tenant).where(*filters)
    )

    result = await db.execute(
        select(Tenant)
        .where(*filters)
        .order_by(Tenant.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    tenants = result.scalars().all()

    items = []
    for tenant in tenants:
        admin_email = await _admin_email_for(tenant.id)
        items.append(
            PlatformTenantOut.model_validate(tenant).model_copy(
                update={"admin_email": admin_email}
            )
        )

    return PaginatedTenants(
        items=items,
        total=total or 0,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil((total or 0) / page_size)),
    )


@router.patch("/tenants/{tenant_id}/subscription", response_model=PlatformTenantOut)
async def set_subscription_enabled(
    tenant_id: uuid.UUID,
    body: SubscriptionUpdate,
    operator: str = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    previous = tenant.subscription_enabled
    tenant.subscription_enabled = body.enabled

    db.add(
        AuditLog(
            tenant_id=tenant.id,
            actor_type="platform_admin",
            action="subscription.enabled" if body.enabled else "subscription.disabled",
            resource_type="tenant",
            resource_id=tenant.id,
            changes={
                "subscription_enabled": {"from": previous, "to": body.enabled},
                "operator": operator,
            },
        )
    )

    await db.commit()
    await db.refresh(tenant)

    admin_email = await _admin_email_for(tenant.id)
    return PlatformTenantOut.model_validate(tenant).model_copy(
        update={"admin_email": admin_email}
    )
