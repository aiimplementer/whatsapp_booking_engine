import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_tenant_db, require_role
from app.models.tenant import Tenant, TenantUser
from app.schemas.tenant import (
    TenantOut,
    TenantUpdate,
    TenantUserCreate,
    TenantUserOut,
    TenantUserRoleUpdate,
)
from app.security import hash_password
from app.services.email_client import EmailSendError, is_configured, send_email

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


@router.get("/me", response_model=TenantOut)
async def get_my_tenant(
    user: TenantUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant = await db.get(Tenant, user.tenant_id)
    return tenant


@router.patch("/me", response_model=TenantOut)
async def update_my_tenant(
    body: TenantUpdate,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant = await db.get(Tenant, user.tenant_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tenant, field, value)
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.post("/me/test-email", status_code=status.HTTP_200_OK)
async def send_test_email(
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Sends a real test email to the tenant's own address (tenant.email)
    right now, bypassing the email_notifications_enabled toggle, and
    surfaces the actual Gmail error instead of the silent best-effort
    behaviour notify_appointment uses for real bookings. Exists purely so
    an admin troubleshooting "I never got an email" has one call that
    tells them exactly what's wrong (unset GMAIL_* env vars, an expired/
    invalid refresh token, wrong sender scope, etc.) instead of having to
    read server logs.
    """
    if not is_configured():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "Gmail is not configured on the server — GMAIL_CLIENT_ID, "
                "GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN and GMAIL_SENDER_EMAIL "
                "must all be set."
            ),
        )
    tenant = await db.get(Tenant, user.tenant_id)
    try:
        await send_email(
            to=tenant.email,
            subject="ScheduleMate test email",
            html_body=(
                "<p>This is a test email from your ScheduleMate admin dashboard. "
                "If you got this, appointment-notification emails are working.</p>"
            ),
        )
    except EmailSendError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return {"sent_to": tenant.email}


@router.get("/me/users", response_model=list[TenantUserOut])
async def list_staff(
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(TenantUser)
        .where(TenantUser.tenant_id == user.tenant_id)
        .order_by(TenantUser.created_at)
    )
    return result.scalars().all()


@router.post(
    "/me/users", response_model=TenantUserOut, status_code=status.HTTP_201_CREATED
)
async def create_staff(
    body: TenantUserCreate,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    new_user = TenantUser(
        tenant_id=user.tenant_id,
        email=body.email,
        role=body.role,
        password_hash=hash_password(body.password),
    )
    db.add(new_user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email is already in use for this tenant",
        ) from exc
    await db.refresh(new_user)
    return new_user


@router.patch("/me/users/{user_id}", response_model=TenantUserOut)
async def update_staff_role(
    user_id: uuid.UUID,
    body: TenantUserRoleUpdate,
    current_user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    target = await db.get(TenantUser, user_id)
    if target is None or target.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if target.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The account owner's role can't be changed",
        )

    if target.id == current_user.id and body.role != "admin":
        await _ensure_not_last_admin(db, current_user.tenant_id, exclude_user_id=target.id)

    target.role = body.role
    await db.commit()
    await db.refresh(target)
    return target


@router.delete("/me/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_staff(
    user_id: uuid.UUID,
    current_user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    target = await db.get(TenantUser, user_id)
    if target is None or target.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if target.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The account owner can't be removed",
        )

    if target.role == "admin":
        await _ensure_not_last_admin(db, current_user.tenant_id, exclude_user_id=target.id)

    await db.delete(target)
    await db.commit()


async def _ensure_not_last_admin(
    db: AsyncSession, tenant_id: uuid.UUID, *, exclude_user_id: uuid.UUID
) -> None:
    """Blocks demoting/removing the last remaining admin — otherwise a tenant
    could lock itself out of its own account with no one left who can invite
    staff or change roles back."""
    result = await db.execute(
        select(func.count())
        .select_from(TenantUser)
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.role == "admin",
            TenantUser.id != exclude_user_id,
        )
    )
    remaining_admins = result.scalar_one()
    if remaining_admins == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove the last remaining admin",
        )
