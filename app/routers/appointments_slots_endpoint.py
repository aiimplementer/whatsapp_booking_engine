"""
Staff-side available slots endpoint for the admin dashboard.

This is separate from the public endpoint (app/routers/public.py::get_available_slots)
because:
1. It requires staff login, not anonymous access
2. It respects the same slot calculation logic via compute_available_slots()
3. It doesn't require the business to have web_booking_enabled (staff can book
   appointments even if customers can't book online)

Both endpoints ultimately call the same compute_available_slots() function from
services/slots.py, so slots shown to staff always match what customers see.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_tenant_db, require_role
from app.models.scheduling import Service
from app.models.tenant import Tenant, TenantUser
from app.schemas.public import AvailableSlotOut
from app.services.slots import compute_available_slots

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments-slots"])

# Staff can always view available slots, even if the business has disabled
# web booking. However, we still enforce a minimum lead time to give staff
# a realistic window — someone can't book an appointment that starts in 30 seconds.
STAFF_MIN_LEAD_MINUTES = 5  # More lenient than the public 15-minute default
# allows for last-minute walk-ins but still prevents obviously-impossible bookings


@router.get("/available-slots", response_model=list[AvailableSlotOut])
async def get_available_slots_for_staff(
    service_id: str | None = Query(default=None),
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Returns available appointment slots for the given service, filtered for
    staff use on the admin dashboard.

    Unlike the public endpoint (which only works if web_booking_enabled is true),
    this always works for authenticated staff members — they should be able to
    book walk-ins and phone appointments regardless of the online booking setting.

    Reuses the same compute_available_slots() function as the public endpoint,
    so slots shown here always match what customers see (modulo the different
    min_lead_minutes).
    """
    tenant = await db.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")

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
        min_lead_minutes=STAFF_MIN_LEAD_MINUTES,
    )
    return [
        AvailableSlotOut(scheduled_at=s.start, duration_minutes=s.duration_minutes)
        for s in slots
    ]
