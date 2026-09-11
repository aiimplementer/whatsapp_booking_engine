import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_db_for_tenant
from app.deps import get_current_user
from app.models.tenant import Tenant, TenantUser
from app.models.scheduling import Service, SchedulingConfig
from app.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
)
from app.security import (
    InvalidTokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, db: AsyncSession = Depends(get_db)):
    """
    Creates a new tenant AND its first admin user, then logs them straight in.
    Matches 'Phase 1: Tenant Registration' in the architecture doc.

    Two separate DB sessions on purpose:
      1. Insert the tenant — this table has no RLS, it's the tenant registry itself.
      2. Insert the first tenant_user WITH the tenant's RLS context set, same as
         every other write to that table will be from here on. No special-casing
         "the very first user" bypasses the same isolation everyone else gets.
    
    Auto-setup:
      3. Create default service and scheduling config so booking page works immediately.
    """
    tenant = Tenant(
        name=body.business_name,
        slug=body.tenant_slug,
        email=body.business_email,
        phone=body.business_phone,
        timezone=body.timezone,
    )
    db.add(tenant)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A tenant with that slug or email already exists",
        ) from exc
    await db.refresh(tenant)

    async for tenant_db in get_db_for_tenant(str(tenant.id)):
        # Create admin user
        admin_user = TenantUser(
            tenant_id=tenant.id,
            email=body.admin_email,
            role="admin",
            password_hash=hash_password(body.admin_password),
        )
        tenant_db.add(admin_user)
        try:
            await tenant_db.commit()
        except IntegrityError as exc:
            await tenant_db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That admin email is already in use for this tenant",
            ) from exc
        await tenant_db.refresh(admin_user)

        # Auto-setup: Create default service
        default_service = Service(
            tenant_id=tenant.id,
            name="General Appointment",
            duration_minutes=30,
            description="Default service for new tenants",
            active=True,
        )
        tenant_db.add(default_service)
        await tenant_db.flush()  # Get the service ID

        # Auto-setup: Create default scheduling config
        # All 7 days, 9am-5pm in tenant's timezone
        # time_slots is a JSONB column, so this is a plain list of dicts,
        # not an ORM model — there's no TimeSlotWindow class in app.models.scheduling.
        time_slots = [
            {"day": 0, "start": "09:00", "end": "17:00"},  # Sunday
            {"day": 1, "start": "09:00", "end": "17:00"},  # Monday
            {"day": 2, "start": "09:00", "end": "17:00"},  # Tuesday
            {"day": 3, "start": "09:00", "end": "17:00"},  # Wednesday
            {"day": 4, "start": "09:00", "end": "17:00"},  # Thursday
            {"day": 5, "start": "09:00", "end": "17:00"},  # Friday
            {"day": 6, "start": "09:00", "end": "17:00"},  # Saturday
        ]

        # service_id=None makes this the tenant-wide default (see
        # SchedulingConfig.service_id docstring / _load_config's fallback
        # logic in app/services/slots.py). Scoping it to default_service.id
        # instead would mean every OTHER service — including ones added
        # later via the admin UI or API — has no config to fall back to and
        # never shows any bookable slots.
        default_config = SchedulingConfig(
            tenant_id=tenant.id,
            service_id=None,
            working_days=127,  # All 7 days bitmask (0b1111111)
            time_slots=time_slots,
            appointment_duration_minutes=30,
            buffer_minutes=0,
            advance_booking_days=30,
        )
        tenant_db.add(default_config)
        try:
            await tenant_db.commit()
        except IntegrityError as exc:
            await tenant_db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create default scheduling config",
            ) from exc

        return TokenResponse(
            access_token=create_access_token(
                user_id=admin_user.id, tenant_id=tenant.id, role=admin_user.role
            ),
            refresh_token=create_refresh_token(
                user_id=admin_user.id, tenant_id=tenant.id, role=admin_user.role
            ),
        )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Tenant).where(Tenant.slug == body.tenant_slug))
    tenant = result.scalar_one_or_none()
    # Same 401 whether the tenant slug is wrong or the password is wrong —
    # never let an attacker distinguish "no such business" from "wrong password"
    # via response differences.
    invalid_creds = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
    )
    if tenant is None:
        raise invalid_creds

    async for tenant_db in get_db_for_tenant(str(tenant.id)):
        result = await tenant_db.execute(
            select(TenantUser).where(TenantUser.email == body.email)
        )
        user = result.scalar_one_or_none()
        if user is None or not verify_password(body.password, user.password_hash):
            raise invalid_creds

        return TokenResponse(
            access_token=create_access_token(
                user_id=user.id, tenant_id=tenant.id, role=user.role
            ),
            refresh_token=create_refresh_token(
                user_id=user.id, tenant_id=tenant.id, role=user.role
            ),
        )


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(body: RefreshRequest):
    try:
        payload = decode_token(body.refresh_token, expected_type=TokenType.REFRESH)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        ) from exc

    # Deliberately NOT re-checking the user still exists in the DB here — that's
    # the trade-off of the "simple, stateless" refresh approach we chose. A
    # disabled/deleted user's refresh token stays valid until it expires
    # (up to refresh_token_expire_days). If that gap matters later, switching
    # to DB-backed revocable refresh tokens is the fix — flagged in README.
    return AccessTokenResponse(
        access_token=create_access_token(
            user_id=uuid.UUID(payload.sub),
            tenant_id=uuid.UUID(payload.tenant_id),
            role=payload.role,
        )
    )


@router.get("/me", response_model=UserOut)
async def me(user: TenantUser = Depends(get_current_user)):
    return user