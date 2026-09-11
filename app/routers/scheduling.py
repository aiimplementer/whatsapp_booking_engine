import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scheduling import RecurringBlockedTime
from app.schemas.scheduling import (
    RecurringBlockedTimeCreate,
    RecurringBlockedTimeOut,
    RecurringBlockedTimeUpdate,
)

from app.deps import get_tenant_db, require_role
from app.models.scheduling import BlockedTime, Holiday, SchedulingConfig, Service
from app.models.tenant import TenantUser
from app.schemas.scheduling import (
    BlockedTimeCreate,
    BlockedTimeOut,
    HolidayCreate,
    HolidayOut,
    SchedulingConfigOut,
    SchedulingConfigUpsert,
    ServiceCreate,
    ServiceOut,
    ServiceUpdate,
)

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])


# ----------------------------------------------------------------------------
# Services
# ----------------------------------------------------------------------------

@router.get("/services", response_model=list[ServiceOut])
async def list_services(
    include_inactive: bool = False,
    user: TenantUser = Depends(require_role("admin", "staff", "viewer")),
    db: AsyncSession = Depends(get_tenant_db),
):
    query = select(Service).where(Service.tenant_id == user.tenant_id)
    if not include_inactive:
        query = query.where(Service.active.is_(True))
    result = await db.execute(query.order_by(Service.name))
    return result.scalars().all()


@router.post(
    "/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED
)
async def create_service(
    body: ServiceCreate,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    service = Service(tenant_id=user.tenant_id, **body.model_dump())
    db.add(service)
    await db.commit()
    await db.refresh(service)
    return service


@router.patch("/services/{service_id}", response_model=ServiceOut)
async def update_service(
    service_id: uuid.UUID,
    body: ServiceUpdate,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    service = await _get_owned(db, Service, service_id, user.tenant_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(service, field, value)
    await db.commit()
    await db.refresh(service)
    return service


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_service(
    service_id: uuid.UUID,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Soft-deletes by setting active=False rather than a hard DELETE — past
    appointments reference this service_id and should keep showing its name
    in history instead of falling back to NULL via ON DELETE SET NULL."""
    service = await _get_owned(db, Service, service_id, user.tenant_id)
    service.active = False
    await db.commit()


# ----------------------------------------------------------------------------
# Scheduling configs — upsert on (tenant_id, service_id)
# ----------------------------------------------------------------------------

@router.get("/configs", response_model=list[SchedulingConfigOut])
async def list_configs(
    user: TenantUser = Depends(require_role("admin", "staff", "viewer")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(SchedulingConfig).where(SchedulingConfig.tenant_id == user.tenant_id)
    )
    return result.scalars().all()


@router.put("/configs", response_model=SchedulingConfigOut)
async def upsert_config(
    body: SchedulingConfigUpsert,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """PUT, not POST: idempotent create-or-replace keyed on
    (tenant_id, service_id), matching the DB's unique constraint of the same
    name. Pass service_id=null to set the tenant-wide default config."""
    result = await db.execute(
        select(SchedulingConfig).where(
            SchedulingConfig.tenant_id == user.tenant_id,
            SchedulingConfig.service_id == body.service_id,
        )
    )
    config = result.scalar_one_or_none()

    payload = body.model_dump()
    payload["time_slots"] = [ts.model_dump() for ts in body.time_slots]

    if config is None:
        config = SchedulingConfig(tenant_id=user.tenant_id, **payload)
        db.add(config)
    else:
        for field, value in payload.items():
            setattr(config, field, value)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A scheduling config for this service already exists",
        ) from exc
    await db.refresh(config)
    return config


# ----------------------------------------------------------------------------
# Holidays
# ----------------------------------------------------------------------------

@router.get("/holidays", response_model=list[HolidayOut])
async def list_holidays(
    user: TenantUser = Depends(require_role("admin", "staff", "viewer")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(Holiday)
        .where(Holiday.tenant_id == user.tenant_id)
        .order_by(Holiday.date)
    )
    return result.scalars().all()


@router.post(
    "/holidays", response_model=HolidayOut, status_code=status.HTTP_201_CREATED
)
async def create_holiday(
    body: HolidayCreate,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    holiday = Holiday(tenant_id=user.tenant_id, **body.model_dump())
    db.add(holiday)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A holiday for that date already exists",
        ) from exc
    await db.refresh(holiday)
    return holiday


@router.delete("/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holiday(
    holiday_id: uuid.UUID,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    holiday = await _get_owned(db, Holiday, holiday_id, user.tenant_id)
    await db.delete(holiday)
    await db.commit()


# ----------------------------------------------------------------------------
# Blocked times
# ----------------------------------------------------------------------------

@router.get("/blocked-times", response_model=list[BlockedTimeOut])
async def list_blocked_times(
    user: TenantUser = Depends(require_role("admin", "staff", "viewer")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(BlockedTime)
        .where(BlockedTime.tenant_id == user.tenant_id)
        .order_by(BlockedTime.start_datetime)
    )
    return result.scalars().all()


@router.post(
    "/blocked-times",
    response_model=BlockedTimeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_blocked_time(
    body: BlockedTimeCreate,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    blocked = BlockedTime(tenant_id=user.tenant_id, **body.model_dump())
    db.add(blocked)
    await db.commit()
    await db.refresh(blocked)
    return blocked


@router.delete("/blocked-times/{blocked_time_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blocked_time(
    blocked_time_id: uuid.UUID,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    blocked = await _get_owned(db, BlockedTime, blocked_time_id, user.tenant_id)
    await db.delete(blocked)
    await db.commit()


async def _get_owned(db: AsyncSession, model, obj_id: uuid.UUID, tenant_id: uuid.UUID):
    """Fetch-by-id with an explicit tenant_id check on top of RLS. RLS already
    prevents cross-tenant reads at the DB level, but checking here too means a
    wrong/missing row surfaces as a clean 404 instead of relying solely on the
    policy to make db.get() return None."""
    obj = await db.get(model, obj_id)
    if obj is None or obj.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return obj


# ============================================================================
# Recurring Blocked Times
# ============================================================================

@router.get("/recurring-blocked-times", response_model=list[RecurringBlockedTimeOut])
async def list_recurring_blocked_times(
    include_inactive: bool = False,
    user: TenantUser = Depends(require_role("admin", "staff", "viewer")),
    db: AsyncSession = Depends(get_tenant_db),
):
    query = select(RecurringBlockedTime).where(
        RecurringBlockedTime.tenant_id == user.tenant_id
    )
    if not include_inactive:
        query = query.where(RecurringBlockedTime.active.is_(True))
    
    result = await db.execute(query.order_by(RecurringBlockedTime.start_time))
    return result.scalars().all()


@router.post(
    "/recurring-blocked-times",
    response_model=RecurringBlockedTimeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_recurring_blocked_time(
    body: RecurringBlockedTimeCreate,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    recurring = RecurringBlockedTime(tenant_id=user.tenant_id, **body.model_dump())
    db.add(recurring)
    await db.commit()
    await db.refresh(recurring)
    return recurring


@router.patch(
    "/recurring-blocked-times/{recurring_blocked_time_id}",
    response_model=RecurringBlockedTimeOut,
)
async def update_recurring_blocked_time(
    recurring_blocked_time_id: uuid.UUID,
    body: RecurringBlockedTimeUpdate,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    recurring = await _get_owned(
        db, RecurringBlockedTime, recurring_blocked_time_id, user.tenant_id
    )
    
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(recurring, field, value)
    
    await db.commit()
    await db.refresh(recurring)
    return recurring


@router.delete(
    "/recurring-blocked-times/{recurring_blocked_time_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_recurring_blocked_time(
    recurring_blocked_time_id: uuid.UUID,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    recurring = await _get_owned(
        db, RecurringBlockedTime, recurring_blocked_time_id, user.tenant_id
    )
    await db.delete(recurring)
    await db.commit()