import secrets
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_tenant_db, require_role
from app.models.appointment import Appointment
from app.models.tenant import TenantUser
from app.schemas.appointment import (
    ALLOWED_TRANSITIONS,
    VALID_STATUSES,
    AppointmentCreate,
    AppointmentOut,
    AppointmentUpdate,
)

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])


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
    if date_from:
        query = query.where(Appointment.scheduled_at >= date_from)
    if date_to:
        query = query.where(Appointment.scheduled_at < date_to)

    query = query.order_by(Appointment.scheduled_at).offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


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

    for field, value in updates.items():
        setattr(appointment, field, value)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _conflict_or_reraise(exc)
    await db.refresh(appointment)
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
