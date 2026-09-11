"""
Enhanced WhatsApp conversational booking flow with interactive date/time selection.

Each customer (tenant_id, customer_phone) has one `whatsapp_sessions` row
that tracks where they are in the conversation (`current_step`) and any
selections made so far (`temp_data`). This module is pure logic — it reads/
mutates the given session object and returns the list of text replies to
send; the router is responsible for persisting the session and actually
calling the Cloud API to send those replies.

Steps: MAIN_MENU -> AWAIT_SERVICE -> AWAIT_DATE -> AWAIT_TIME -> AWAIT_NAME -> (booked, back to MAIN_MENU)

Features:
- 7-day date grouping with forward/backward navigation
- Time slots dynamically retrieved for each selected date
- Customer name included in confirmation message
"""

import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.scheduling import Service
from app.models.tenant import Tenant
from app.models.whatsapp_session import WhatsAppSession
from app.services.slots import compute_available_slots

MENU_TEXT = (
    "Hi! I can help you book an appointment.\n\n"
    "Reply *1* to book an appointment\n"
    "Reply *2* to check a booking (send your booking reference)\n"
    "Reply *menu* any time to see this again."
)


def _generate_booking_ref() -> str:
    return f"APT-{secrets.token_hex(4).upper()}"


def _reset(session: WhatsAppSession) -> None:
    session.current_step = "MAIN_MENU"
    session.temp_data = {}


async def handle_incoming_message(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str
) -> list[str]:
    text = (text or "").strip()
    lowered = text.lower()

    if lowered in ("menu", "hi", "hello", "start"):
        _reset(session)
        return [MENU_TEXT]

    if session.current_step == "MAIN_MENU":
        return await _handle_main_menu(db, tenant, session, lowered)
    if session.current_step == "AWAIT_SERVICE":
        return await _handle_await_service(db, tenant, session, text)
    if session.current_step == "AWAIT_DATE":
        return await _handle_await_date(db, tenant, session, text)
    if session.current_step == "AWAIT_TIME":
        return await _handle_await_time(db, tenant, session, text)
    if session.current_step == "AWAIT_NAME":
        return await _handle_await_name(db, tenant, session, text)

    # Unknown/stale step — don't get the customer stuck.
    _reset(session)
    return [MENU_TEXT]


async def _handle_main_menu(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, lowered: str
) -> list[str]:
    if lowered == "1" or "book" in lowered:
        result = await db.execute(
            select(Service).where(Service.tenant_id == tenant.id, Service.active.is_(True))
        )
        services = result.scalars().all()
        if not services:
            return ["Sorry, no services are available for booking right now."]
        session.current_step = "AWAIT_SERVICE"
        session.temp_data = {"service_ids": [str(s.id) for s in services]}
        lines = [f"{i+1}. {s.name} ({s.duration_minutes} min)" for i, s in enumerate(services)]
        return ["Which service would you like?\n\n" + "\n".join(lines)]

    if lowered == "2" or "check" in lowered or lowered.startswith("apt-"):
        booking_ref = text_upper_if_ref(lowered)
        if not booking_ref:
            session.current_step = "MAIN_MENU"
            return ["Please send your booking reference, e.g. APT-A1B2C3D4."]
        return await _lookup_booking(db, tenant, session, booking_ref)

    return [MENU_TEXT]


def text_upper_if_ref(lowered: str) -> str | None:
    if lowered.startswith("apt-"):
        return lowered.upper()
    return None


async def _lookup_booking(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, booking_ref: str
) -> list[str]:
    result = await db.execute(
        select(Appointment).where(
            Appointment.tenant_id == tenant.id,
            Appointment.booking_ref == booking_ref,
            Appointment.customer_phone == session.customer_phone,
        )
    )
    appointment = result.scalar_one_or_none()
    _reset(session)
    if appointment is None:
        return ["I couldn't find a booking with that reference on this number."]
    tz = ZoneInfo(tenant.timezone)
    local_time = appointment.scheduled_at.astimezone(tz)
    return [
        f"Booking {appointment.booking_ref}: {appointment.status}\n"
        f"Name: {appointment.customer_name}\n"
        f"{local_time.strftime('%a %d %b, %I:%M %p')}"
    ]


async def _handle_await_service(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str
) -> list[str]:
    service_ids = session.temp_data.get("service_ids", [])
    idx = _parse_index(text, len(service_ids))
    if idx is None:
        return ["Please reply with the number next to the service you'd like."]

    service_id = service_ids[idx]
    service = await db.get(Service, service_id)
    
    # Get all available dates for this service
    slots = await compute_available_slots(
        db, tenant_id=tenant.id, tenant_timezone=tenant.timezone,
        service_id=service.id, min_lead_minutes=15,
    )
    
    if not slots:
        _reset(session)
        return ["No upcoming slots are available for that service right now. Reply *menu* to start over."]

    # Transition to date selection
    session.current_step = "AWAIT_DATE"
    session.temp_data = {
        "service_id": str(service.id),
        "service_name": service.name,
        "duration_minutes": service.duration_minutes,
        "all_slots": [s.start.isoformat() for s in slots],  # Store all slots for later
        "date_page": 0,  # Current page of 7-day groups
    }
    
    return _render_date_page(tenant.timezone, session.temp_data)


def _render_date_page(timezone: str, temp_data: dict) -> list[str]:
    """Render a 7-day date page with pagination controls."""
    all_slots = temp_data.get("all_slots", [])
    date_page = temp_data.get("date_page", 0)
    
    # Extract unique dates from slots
    tz = ZoneInfo(timezone)
    unique_dates = []
    seen_dates = set()
    for slot_iso in all_slots:
        slot_dt = datetime.fromisoformat(slot_iso).astimezone(tz)
        slot_date = slot_dt.date()
        if slot_date not in seen_dates:
            unique_dates.append(slot_date)
            seen_dates.add(slot_date)
    
    # Get 7-day window for current page
    start_idx = date_page * 7
    end_idx = start_idx + 7
    page_dates = unique_dates[start_idx:end_idx]
    
    if not page_dates:
        return ["No more dates available. Reply *menu* to start over."]
    
    # Render date options
    lines = []
    for i, d in enumerate(page_dates):
        date_str = d.strftime('%a, %d-%b-%y')
        lines.append(f"{i+1}. {date_str}")
    
    # Add pagination controls
    has_prev = date_page > 0
    has_next = end_idx < len(unique_dates)
    
    if has_prev or has_next:
        lines.append("")  # Separator
        if has_prev:
            lines.append("Reply *0* for previous 7 days")
        if has_next:
            lines.append(f"Reply *{len(page_dates) + 1}* for next 7 days")
    
    message = "Which day is the appointment on?\n\n" + "\n".join(lines)
    return [message]


async def _handle_await_date(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str
) -> list[str]:
    text = text.strip()
    temp_data = session.temp_data
    date_page = temp_data.get("date_page", 0)
    
    # Extract unique dates
    all_slots = temp_data.get("all_slots", [])
    tz = ZoneInfo(tenant.timezone)
    unique_dates = []
    seen_dates = set()
    for slot_iso in all_slots:
        slot_dt = datetime.fromisoformat(slot_iso).astimezone(tz)
        slot_date = slot_dt.date()
        if slot_date not in seen_dates:
            unique_dates.append(slot_date)
            seen_dates.add(slot_date)
    
    # Handle pagination
    if text == "0":
        # Previous page
        if date_page > 0:
            temp_data["date_page"] = date_page - 1
            return _render_date_page(tenant.timezone, temp_data)
        else:
            return ["You're already on the first page. Please select a date (1-7)."]
    
    start_idx = date_page * 7
    end_idx = start_idx + 7
    page_dates = unique_dates[start_idx:end_idx]
    
    # Check for "next" button
    next_button = len(page_dates) + 1
    if text == str(next_button):
        if end_idx < len(unique_dates):
            temp_data["date_page"] = date_page + 1
            return _render_date_page(tenant.timezone, temp_data)
        else:
            return ["No more dates available."]
    
    # Handle date selection
    idx = _parse_index(text, len(page_dates))
    if idx is None:
        return ["Please reply with the number next to the date you'd like."]
    
    selected_date = page_dates[idx]
    
    # Filter slots for this date and get unique times
    selected_slots = []
    for slot_iso in all_slots:
        slot_dt = datetime.fromisoformat(slot_iso).astimezone(tz)
        if slot_dt.date() == selected_date:
            selected_slots.append(slot_dt)
    
    if not selected_slots:
        return ["No time slots available for that date. Please pick another date."]
    
    # Sort by time
    selected_slots.sort(key=lambda x: x.time())
    
    # Transition to time selection
    session.current_step = "AWAIT_TIME"
    session.temp_data = {
        **temp_data,
        "selected_date": selected_date.isoformat(),
        "time_slots": [s.isoformat() for s in selected_slots],
    }
    
    # Render times for this date
    lines = []
    for i, slot_dt in enumerate(selected_slots):
        time_str = slot_dt.strftime('%I:%M %p').lstrip('0')  # Remove leading 0 from hour
        lines.append(f"{i+1}. {time_str}")
    
    date_display = selected_date.strftime('%a, %d-%b-%y')
    message = f"What time on {date_display}?\n\n" + "\n".join(lines)
    return [message]


async def _handle_await_time(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str
) -> list[str]:
    time_slots = session.temp_data.get("time_slots", [])
    idx = _parse_index(text, len(time_slots))
    if idx is None:
        return ["Please reply with the number next to the time you'd like."]

    chosen_slot_iso = time_slots[idx]
    session.temp_data = {**session.temp_data, "chosen_slot": chosen_slot_iso}
    session.current_step = "AWAIT_NAME"
    return ["What name should this booking be under?"]


async def _handle_await_name(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str
) -> list[str]:
    name = text.strip()
    if not name:
        return ["Please send a name for the booking."]

    data = session.temp_data
    scheduled_at = datetime.fromisoformat(data["chosen_slot"])
    appointment = Appointment(
        tenant_id=tenant.id,
        service_id=data["service_id"],
        customer_name=name,
        customer_phone=session.customer_phone,
        scheduled_at=scheduled_at,
        duration_minutes=data["duration_minutes"],
        status="CONFIRMED",
        booking_ref=_generate_booking_ref(),
    )
    db.add(appointment)
    from sqlalchemy.exc import IntegrityError

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        _reset(session)
        return ["Sorry, that time was just taken. Reply *1* to see current availability."]

    tz = ZoneInfo(tenant.timezone)
    local_time = scheduled_at.astimezone(tz)
    confirmation = (
        f"✓ Confirmed!\n\n"
        f"Name: {name}\n"
        f"Service: {data['service_name']}\n"
        f"Date & Time: {local_time.strftime('%a, %d %b at %I:%M %p')}\n"
        f"Reference: {appointment.booking_ref}\n\n"
        f"Reply *menu* for anything else."
    )
    _reset(session)
    return [confirmation]


def _parse_index(text: str, count: int) -> int | None:
    text = text.strip()
    if not text.isdigit():
        return None
    idx = int(text) - 1
    if 0 <= idx < count:
        return idx
    return None