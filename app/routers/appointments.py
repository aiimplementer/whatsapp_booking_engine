import secrets
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_tenant_db, require_role
from app.models.appointment import Appointment
from app.models.tenant import Tenant, TenantUser
from app.schemas.appointment import (
    ALLOWED_TRANSITIONS,
    VALID_STATUSES,
    AppointmentCreate,
    AppointmentOut,
    AppointmentUpdate,
)
from app.services.email_client import notify_appointment
from app.services.slots import validate_business_hours

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])

# Kept in one place because both the calendar-summary endpoint below and the
# admin dashboard's date-header counts (admin_dashboard.js) need to agree on
# what "active" means — cancelled/no-show/expired appointments still exist
# in the data but shouldn't count as "on the books" for a day.
INACTIVE_STATUSES = ("CANCELLED", "NO_SHOW", "EXPIRED")


def _generate_booking_ref() -> str:
    # APT-XXXXXXXX, 8 uppercase hex chars — short enough to read over the
    # phone/WhatsApp, long enough that collisions are practically impossible
    # even before the DB's unique constraint has the final say.
    return f"APT-{secrets.token_hex(4).upper()}"


@router.get("", response_model=list[AppointmentOut])
async def list_appointments(
    status_filter: str | None = Query(default=None, alias="status"),
    customer_phone: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    user: TenantUser = Depends(require_role("admin", "staff", "viewer")),
    db: AsyncSession = Depends(get_tenant_db),
):
    if status_filter is not None and status_filter not in VALID_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid status filter")

    query = select(Appointment).where(Appointment.tenant_id == user.tenant_id)
    if status_filter:
        query = query.where(Appointment.status == status_filter)
    if customer_phone:
        # Same '+'-tolerant comparison as the public lookup endpoint (see
        # app/routers/public.py::_normalize_phone) — a staff member searching
        # shouldn't need to remember whether this particular customer's
        # number was saved with or without a country-code '+'.
        normalized = customer_phone.strip().lstrip("+")
        query = query.where(func.replace(Appointment.customer_phone, "+", "") == normalized)
    if date_from or date_to:
        # date_from/date_to are calendar dates as the tenant's admin sees them
        # (from the "From"/"To" pickers) — they need to be interpreted in the
        # tenant's own timezone, not UTC, and date_to needs to include the
        # whole of that day. Comparing the bare `date` straight against the
        # tz-aware `scheduled_at` column (as this used to do) makes Postgres
        # treat the boundary as UTC midnight, and the old `< date_to` made
        # the end date exclusive — so filtering "From: 12 Sep, To: 12 Sep"
        # (the natural way to ask for one day) always returned zero rows.
        tenant = await db.get(Tenant, user.tenant_id)
        tz = ZoneInfo(tenant.timezone)
        if date_from:
            start = datetime.combine(date_from, time.min, tzinfo=tz)
            query = query.where(Appointment.scheduled_at >= start)
        if date_to:
            end_exclusive = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
            query = query.where(Appointment.scheduled_at < end_exclusive)

    query = query.order_by(Appointment.scheduled_at).offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/calendar-summary")
async def calendar_summary(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: TenantUser = Depends(require_role("admin", "staff", "viewer")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Per-day active-appointment counts for one calendar month, for the
    monthly calendar page. Aggregated in SQL rather than shipping every
    appointment row to the browser — a busy tenant can have well over the
    200-row page cap on GET /appointments in a single month.

    Days are grouped by the tenant's local calendar date (via Postgres'
    `timezone()`, converting the tz-aware `scheduled_at` to a naive local
    timestamp before taking its date) for the same reason the list endpoint's
    date_from/date_to now do: UTC day boundaries don't match business-day
    boundaries once the tenant isn't in UTC.
    """
    tenant = await db.get(Tenant, user.tenant_id)
    tz = ZoneInfo(tenant.timezone)

    start = datetime(year, month, 1, tzinfo=tz)
    end_exclusive = (
        datetime(year + 1, 1, 1, tzinfo=tz) if month == 12 else datetime(year, month + 1, 1, tzinfo=tz)
    )

    local_day = func.date(func.timezone(tenant.timezone, Appointment.scheduled_at))
    query = (
        select(local_day.label("day"), func.count().label("count"))
        .where(
            Appointment.tenant_id == user.tenant_id,
            Appointment.scheduled_at >= start,
            Appointment.scheduled_at < end_exclusive,
            Appointment.status.notin_(INACTIVE_STATUSES),
        )
        .group_by(local_day)
    )
    result = await db.execute(query)
    counts = {day.isoformat(): count for day, count in result.all()}

    return {"year": year, "month": month, "timezone": tenant.timezone, "counts": counts}


@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(
    appointment_id: uuid.UUID,
    user: TenantUser = Depends(require_role("admin", "staff", "viewer")),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await _get_owned(db, appointment_id, user.tenant_id)


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    body: AppointmentCreate,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    if body.status not in VALID_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid status")

    tenant = await db.get(Tenant, user.tenant_id)
    reason = await validate_business_hours(
        db,
        tenant_id=user.tenant_id,
        tenant_timezone=tenant.timezone,
        service_id=body.service_id,
        scheduled_at=body.scheduled_at,
        duration_minutes=body.duration_minutes,
    )
    if reason is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=reason)

    appointment = Appointment(
        tenant_id=user.tenant_id,
        booking_ref=_generate_booking_ref(),
        **body.model_dump(),
    )
    db.add(appointment)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _conflict_or_reraise(exc)
    await db.refresh(appointment)
    # Notify the business even though staff entered this one themselves —
    # bookings are often taken by an employee, not the owner, so the owner
    # (tenant.email) still wants to know a slot just got filled.
    await notify_appointment(appointment=appointment, tenant=tenant, event="booked")
    return appointment


@router.patch("/{appointment_id}", response_model=AppointmentOut)
async def update_appointment(
    appointment_id: uuid.UUID,
    body: AppointmentUpdate,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    appointment = await _get_owned(db, appointment_id, user.tenant_id)
    updates = body.model_dump(exclude_unset=True)

    if "status" in updates:
        new_status = updates["status"]
        if new_status not in VALID_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid status")
        if new_status != appointment.status and new_status not in ALLOWED_TRANSITIONS.get(
            appointment.status, set()
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Cannot move status from {appointment.status} to {new_status}",
            )

    # Only re-check business hours when the reschedule actually touches
    # when/how-long the appointment is — a pure status change (e.g.
    # CONFIRMED -> CHECKED_IN) shouldn't be blocked by hours that were
    # already valid when the appointment was first created.
    if "scheduled_at" in updates or "duration_minutes" in updates:
        tenant = await db.get(Tenant, user.tenant_id)
        reason = await validate_business_hours(
            db,
            tenant_id=user.tenant_id,
            tenant_timezone=tenant.timezone,
            service_id=appointment.service_id,
            scheduled_at=updates.get("scheduled_at", appointment.scheduled_at),
            duration_minutes=updates.get("duration_minutes", appointment.duration_minutes),
        )
        if reason is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=reason)

    became_cancelled = updates.get("status") == "CANCELLED" and appointment.status != "CANCELLED"

    for field, value in updates.items():
        setattr(appointment, field, value)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _conflict_or_reraise(exc)
    await db.refresh(appointment)
    if became_cancelled:
        tenant = await db.get(Tenant, user.tenant_id)
        await notify_appointment(appointment=appointment, tenant=tenant, event="cancelled")
    return appointment


@router.delete("/{appointment_id}", response_model=AppointmentOut)
async def cancel_appointment(
    appointment_id: uuid.UUID,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Soft-cancel, not a hard DELETE — appointment history (for reporting,
    audit, and repeat-customer lookups) should survive a cancellation."""
    appointment = await _get_owned(db, appointment_id, user.tenant_id)
    if "CANCELLED" not in ALLOWED_TRANSITIONS.get(appointment.status, set()):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel an appointment in status {appointment.status}",
        )
    appointment.status = "CANCELLED"
    await db.commit()
    await db.refresh(appointment)
    tenant = await db.get(Tenant, user.tenant_id)
    await notify_appointment(appointment=appointment, tenant=tenant, event="cancelled")
    return appointment


async def _get_owned(
    db: AsyncSession, appointment_id: uuid.UUID, tenant_id: uuid.UUID
) -> Appointment:
    appointment = await db.get(Appointment, appointment_id)
    if appointment is None or appointment.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    return appointment


def _conflict_or_reraise(exc: IntegrityError) -> HTTPException:
    # excl_appointments_no_overlap is the only constraint on this table likely
    # to fire under normal use (booking_ref collisions are astronomically
    # rare) — surface it as a clean 409 rather than a raw 500.
    if "excl_appointments_no_overlap" in str(exc.orig):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This time slot overlaps an existing appointment",
        )
    return HTTPException(status.HTTP_409_CONFLICT, detail="Could not save appointment")
