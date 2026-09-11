"""
Computes bookable appointment slots on the fly.

This is deliberately the ONE place slot logic lives. Today the public booking
router calls compute_available_slots() directly per-request. The planned
nightly job that populates the `available_slots` cache table should import
and call this same function rather than re-implementing the logic — the cache
table is a read-optimization, not a second source of truth.

Timezone handling: scheduling_configs' time_slots are wall-clock times
("09:00") in the tenant's local timezone (tenants.timezone). Everything is
converted to UTC-aware datetimes before being compared against
appointments.scheduled_at / blocked_times, which are stored as TIMESTAMPTZ.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.scheduling import BlockedTime, Holiday, RecurringBlockedTime, SchedulingConfig


@dataclass(frozen=True)
class Slot:
    start: datetime  # UTC-aware
    duration_minutes: int


def _bit_day(d: date) -> int:
    """0=Sun..6=Sat, matching the working_days bitmask convention used in
    the schema (bit0=Sun..bit6=Sat). Python's date.weekday() is Mon=0..Sun=6,
    so shift by one and wrap."""
    return (d.weekday() + 1) % 7


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":")
    return int(hh), int(mm)


async def _load_config(
    db: AsyncSession, tenant_id: uuid.UUID, service_id: uuid.UUID | None
) -> SchedulingConfig | None:
    """Service-specific config wins; falls back to the tenant-wide default
    (service_id IS NULL) if no override exists for this service."""
    if service_id is not None:
        result = await db.execute(
            select(SchedulingConfig).where(
                SchedulingConfig.tenant_id == tenant_id,
                SchedulingConfig.service_id == service_id,
            )
        )
        config = result.scalar_one_or_none()
        if config is not None:
            return config

    result = await db.execute(
        select(SchedulingConfig).where(
            SchedulingConfig.tenant_id == tenant_id,
            SchedulingConfig.service_id.is_(None),
        )
    )
    return result.scalar_one_or_none()


"""
UPDATED compute_available_slots() function to include RecurringBlockedTime support.

Replace the existing async def compute_available_slots() in app/services/slots.py
with this updated version. The key addition is loading recurring_blocked_times and
expanding them into a full list of (start, end) UTC ranges before checking overlaps.
"""

async def compute_available_slots(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    tenant_timezone: str,
    service_id: uuid.UUID | None,
    now: datetime | None = None,
    min_lead_minutes: int = 0,
) -> list[Slot]:
    """Returns open slots between now and the config's advance_booking_days,
    excluding holidays, blocked times, recurring blocked times, and slots that 
    overlap an existing active (PENDING/CONFIRMED/CHECKED_IN) appointment.

    Note: overlap is checked tenant-wide, not per-service — this matches the
    DB's excl_appointments_no_overlap constraint, which has no service_id
    column, so double-booking is treated as a whole-business scheduling
    conflict rather than a per-service one.
    """
    config = await _load_config(db, tenant_id, service_id)
    if config is None:
        return []

    tz = ZoneInfo(tenant_timezone)
    now = (now or datetime.now(tz=ZoneInfo("UTC"))).astimezone(tz)
    earliest = now + timedelta(minutes=min_lead_minutes)
    horizon_end = (now + timedelta(days=config.advance_booking_days)).date()

    # Load holiday dates
    holidays_result = await db.execute(
        select(Holiday.date).where(
            Holiday.tenant_id == tenant_id,
            Holiday.date >= now.date(),
            Holiday.date <= horizon_end,
        )
    )
    holiday_dates = {row[0] for row in holidays_result.all()}

    range_start_utc = now.astimezone(ZoneInfo("UTC"))
    range_end_utc = datetime.combine(
        horizon_end, datetime.max.time(), tzinfo=tz
    ).astimezone(ZoneInfo("UTC"))

    # Load one-off blocked times
    blocked_result = await db.execute(
        select(BlockedTime.start_datetime, BlockedTime.end_datetime).where(
            BlockedTime.tenant_id == tenant_id,
            BlockedTime.end_datetime > range_start_utc,
            BlockedTime.start_datetime < range_end_utc,
        )
    )
    blocked_ranges = list(blocked_result.all())

    # NEW: Load recurring blocked times and expand them
    recurring_result = await db.execute(
        select(
            RecurringBlockedTime.start_time,
            RecurringBlockedTime.end_time,
            RecurringBlockedTime.days_of_week,
            RecurringBlockedTime.start_date,
            RecurringBlockedTime.end_date,
        ).where(
            RecurringBlockedTime.tenant_id == tenant_id,
            RecurringBlockedTime.active.is_(True),
        )
    )
    
    # Expand recurring blocks into actual datetime ranges
    for start_time, end_time, days_bitmask, recur_start_date, recur_end_date in recurring_result.all():
        recur_start_date = recur_start_date or now.date()
        recur_end_date = recur_end_date or horizon_end
        
        # Clamp to our search window
        recur_start_date = max(recur_start_date, now.date())
        recur_end_date = min(recur_end_date, horizon_end)
        
        # Iterate through each day in the recurrence window
        current = recur_start_date
        while current <= recur_end_date:
            # Check if this day of the week is in the bitmask
            bit = _bit_day(current)
            if (days_bitmask >> bit) & 1:
                # Expand into UTC datetime range
                hh, mm = _parse_hhmm(start_time)
                block_start_local = datetime.combine(
                    current, datetime.min.time(), tzinfo=tz
                ).replace(hour=hh, minute=mm)
                
                hh, mm = _parse_hhmm(end_time)
                block_end_local = datetime.combine(
                    current, datetime.min.time(), tzinfo=tz
                ).replace(hour=hh, minute=mm)
                
                block_start_utc = block_start_local.astimezone(ZoneInfo("UTC"))
                block_end_utc = block_end_local.astimezone(ZoneInfo("UTC"))
                
                # Only add if it overlaps with our search range
                if block_end_utc > range_start_utc and block_start_utc < range_end_utc:
                    blocked_ranges.append((block_start_utc, block_end_utc))
            
            current += timedelta(days=1)

    # Load busy appointments
    busy_result = await db.execute(
        select(Appointment.scheduled_at, Appointment.duration_minutes).where(
            Appointment.tenant_id == tenant_id,
            Appointment.status.in_(["PENDING", "CONFIRMED", "CHECKED_IN"]),
            Appointment.scheduled_at < range_end_utc,
        )
    )
    busy_ranges = [
        (sched_at, sched_at + timedelta(minutes=dur))
        for sched_at, dur in busy_result.all()
        if sched_at + timedelta(minutes=dur) > range_start_utc
    ]

    def overlaps_any(start: datetime, end: datetime, ranges) -> bool:
        return any(start < r_end and end > r_start for r_start, r_end in ranges)

    slot_len = config.appointment_duration_minutes
    step = slot_len + config.buffer_minutes

    slots: list[Slot] = []
    current_date = now.date()
    while current_date <= horizon_end:
        if current_date not in holiday_dates:
            bit = _bit_day(current_date)
            if (config.working_days >> bit) & 1:
                for window in config.time_slots:
                    start_h, start_m = _parse_hhmm(window["start"])
                    end_h, end_m = _parse_hhmm(window["end"])
                    if window.get("day") != bit:
                        continue
                    window_start = datetime.combine(
                        current_date, datetime.min.time(), tzinfo=tz
                    ).replace(hour=start_h, minute=start_m)
                    window_end = datetime.combine(
                        current_date, datetime.min.time(), tzinfo=tz
                    ).replace(hour=end_h, minute=end_m)

                    cursor = window_start
                    while cursor + timedelta(minutes=slot_len) <= window_end:
                        slot_start_utc = cursor.astimezone(ZoneInfo("UTC"))
                        slot_end_utc = slot_start_utc + timedelta(minutes=slot_len)
                        if slot_start_utc >= earliest.astimezone(ZoneInfo("UTC")):
                            if not overlaps_any(
                                slot_start_utc, slot_end_utc, blocked_ranges
                            ) and not overlaps_any(
                                slot_start_utc, slot_end_utc, busy_ranges
                            ):
                                slots.append(
                                    Slot(start=slot_start_utc, duration_minutes=slot_len)
                                )
                        cursor += timedelta(minutes=step)
        current_date += timedelta(days=1)

    return slots
