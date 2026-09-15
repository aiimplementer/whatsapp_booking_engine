import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_public_tenant_db, resolve_tenant_by_slug
from app.models.appointment import Appointment
from app.models.scheduling import Service, SchedulingConfig
from app.models.tenant import Tenant
from app.schemas.public import (
    AvailableSlotOut,
    PublicBookingCreate,
    PublicBookingOut,
    PublicTenantOut,
)
from app.services.business_hours import working_hours_by_day
from app.services.slots import compute_available_slots

router = APIRouter(prefix="/api/v1/public/{tenant_slug}", tags=["public-booking"])

# How close to "now" a customer can book — protects against a slot being
# offered that staff can't realistically prepare for.
DEFAULT_MIN_LEAD_MINUTES = 15
# How much clock drift/rounding to tolerate between the slot the customer was
# shown and the scheduled_at they submit, without re-running slot generation
# synchronously on every booking POST.
SLOT_MATCH_TOLERANCE = timedelta(seconds=1)


def _generate_booking_ref() -> str:
    return f"APT-{secrets.token_hex(4).upper()}"


@router.get("", response_model=PublicTenantOut)
async def get_business_info(
    tenant: Tenant = Depends(resolve_tenant_by_slug),
    db: AsyncSession = Depends(get_public_tenant_db),
):
    if not tenant.web_booking_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Online booking is not available")

    result = await db.execute(
        select(Service).where(Service.tenant_id == tenant.id, Service.active.is_(True))
    )
    services = result.scalars().all()

    # Tenant-wide working hours (service_id IS NULL) — same "Applies to:
    # Tenant-wide default" config set on the admin dashboard's Scheduling >
    # Working hours tab. Per-service overrides aren't surfaced here since
    # the public page doesn't have a per-service hours view yet.
    config_result = await db.execute(
        select(SchedulingConfig).where(
            SchedulingConfig.tenant_id == tenant.id,
            SchedulingConfig.service_id.is_(None),
        )
    )
    config = config_result.scalar_one_or_none()

    return PublicTenantOut(
        name=tenant.name,
        slug=tenant.slug,
        timezone=tenant.timezone,
        branding=tenant.branding,
        services=services,
        cancellation_policy=tenant.cancellation_policy,
        offers=tenant.offers,
        announcements=tenant.announcements,
        working_hours=working_hours_by_day(config) if config else [],
    )


@router.get("/available-slots", response_model=list[AvailableSlotOut])
async def get_available_slots(
    tenant: Tenant = Depends(resolve_tenant_by_slug),
    db: AsyncSession = Depends(get_public_tenant_db),
    service_id: str | None = Query(default=None),
):
    if not tenant.web_booking_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Online booking is not available")

    service_uuid = None
    if service_id is not None:
        service = await db.get(Service, service_id)
        if service is None or service.tenant_id != tenant.id or not service.active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Service not found")
        service_uuid = service.id

    slots = await compute_available_slots(
        db,
        tenant_id=tenant.id,
        tenant_timezone=tenant.timezone,
        service_id=service_uuid,
        min_lead_minutes=DEFAULT_MIN_LEAD_MINUTES,
    )
    return [
        AvailableSlotOut(scheduled_at=s.start, duration_minutes=s.duration_minutes)
        for s in slots
    ]


@router.post(
    "/appointments", response_model=PublicBookingOut, status_code=status.HTTP_201_CREATED
)
async def create_booking(
    body: PublicBookingCreate,
    tenant: Tenant = Depends(resolve_tenant_by_slug),
    db: AsyncSession = Depends(get_public_tenant_db),
):
    if not tenant.web_booking_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Online booking is not available")

    service = None
    if body.service_id is not None:
        service = await db.get(Service, body.service_id)
        if service is None or service.tenant_id != tenant.id or not service.active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Service not found")

    # Re-validate the requested time against freshly computed slots rather
    # than trusting whatever the client last fetched — closes the window
    # where holidays/blocked-times/configs changed between GET and POST.
    # The EXCLUDE constraint is still the final word for concurrent bookings
    # of the *same* slot; this check is what makes stale/invalid slots (past
    # cutoff, outside working hours, etc.) fail with a clear message instead
    # of a raw DB conflict.
    slots = await compute_available_slots(
        db,
        tenant_id=tenant.id,
        tenant_timezone=tenant.timezone,
        service_id=service.id if service else None,
        min_lead_minutes=DEFAULT_MIN_LEAD_MINUTES,
    )
    matching = next(
        (s for s in slots if abs(s.start - body.scheduled_at) <= SLOT_MATCH_TOLERANCE),
        None,
    )
    if matching is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="That time is no longer available — please pick another slot",
        )

    appointment = Appointment(
        tenant_id=tenant.id,
        service_id=service.id if service else None,
        customer_name=body.customer_name,
        customer_phone=body.customer_phone,
        customer_email=body.customer_email,
        scheduled_at=matching.start,
        duration_minutes=matching.duration_minutes,
        notes=body.notes,
        status="CONFIRMED",
        booking_ref=_generate_booking_ref(),
    )
    db.add(appointment)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "excl_appointments_no_overlap" in str(exc.orig):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="That time was just booked by someone else — please pick another slot",
            ) from exc
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Could not create booking"
        ) from exc
    await db.refresh(appointment)
    return appointment


@router.get("/appointments/{booking_ref}", response_model=PublicBookingOut)
async def lookup_booking(
    booking_ref: str,
    customer_phone: str,
    tenant: Tenant = Depends(resolve_tenant_by_slug),
    db: AsyncSession = Depends(get_public_tenant_db),
):
    appointment = await _find_by_ref_and_phone(db, tenant, booking_ref, customer_phone)
    return appointment


@router.post("/appointments/{booking_ref}/cancel", response_model=PublicBookingOut)
async def cancel_booking(
    booking_ref: str,
    customer_phone: str,
    tenant: Tenant = Depends(resolve_tenant_by_slug),
    db: AsyncSession = Depends(get_public_tenant_db),
):
    appointment = await _find_by_ref_and_phone(db, tenant, booking_ref, customer_phone)
    if appointment.status not in ("PENDING", "CONFIRMED"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a booking in status {appointment.status}",
        )
    appointment.status = "CANCELLED"
    await db.commit()
    await db.refresh(appointment)
    return appointment


def _normalize_phone(value: str) -> str:
    """Ignore a leading '+' and incidental whitespace when comparing phone
    numbers. '+919876543210' and '919876543210' are the same customer typing
    the same number two different (both valid) ways — one via the admin
    dashboard, one on this lookup form — and shouldn't be treated as a
    mismatch. This does NOT do any fuzzy/partial matching: every digit still
    has to match exactly, so it can't surface a different customer's booking."""
    return value.strip().lstrip("+")


async def _find_by_ref_and_phone(
    db: AsyncSession, tenant: Tenant, booking_ref: str, customer_phone: str
) -> Appointment:
    result = await db.execute(
        select(Appointment).where(
            Appointment.tenant_id == tenant.id,
            Appointment.booking_ref == booking_ref,
        )
    )
    appointment = result.scalar_one_or_none()
    # Requiring the phone to match (not just the booking_ref) stops anyone
    # who guesses/observes a booking_ref from viewing or cancelling someone
    # else's appointment — booking_ref alone isn't treated as a secret.
    if appointment is None or _normalize_phone(appointment.customer_phone) != _normalize_phone(
        customer_phone
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking not found")
    return appointment
