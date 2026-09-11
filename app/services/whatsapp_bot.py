"""
Minimal WhatsApp conversational booking flow.

Each customer (tenant_id, customer_phone) has one `whatsapp_sessions` row
that tracks where they are in the conversation (`current_step`) and any
selections made so far (`temp_data`). This module is pure logic — it reads/
mutates the given session object and returns the list of text replies to
send; the router is responsible for persisting the session and actually
calling the Cloud API to send those replies.

Steps: MAIN_MENU -> AWAIT_SERVICE -> AWAIT_SLOT -> AWAIT_NAME -> (booked, back to MAIN_MENU)
"""

import secrets
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.scheduling import Service
from app.models.tenant import Tenant
from app.models.whatsapp_session import WhatsAppSession
from app.services.slots import compute_available_slots

MAX_LISTED_SLOTS = 6
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
    if session.current_step == "AWAIT_SLOT":
        return await _handle_await_slot(db, tenant, session, text)
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
    slots = await compute_available_slots(
        db, tenant_id=tenant.id, tenant_timezone=tenant.timezone,
        service_id=service.id, min_lead_minutes=15,
    )
    slots = slots[:MAX_LISTED_SLOTS]
    if not slots:
        _reset(session)
        return ["No upcoming slots are available for that service right now. Reply *menu* to start over."]

    tz = ZoneInfo(tenant.timezone)
    session.current_step = "AWAIT_SLOT"
    session.temp_data = {
        "service_id": str(service.id),
        "service_name": service.name,
        "duration_minutes": service.duration_minutes,
        "slot_options": [s.start.isoformat() for s in slots],
    }
    lines = [
        f"{i+1}. {s.start.astimezone(tz).strftime('%a %d %b, %I:%M %p')}"
        for i, s in enumerate(slots)
    ]
    return [f"Great, {service.name}. Pick a time:\n\n" + "\n".join(lines)]


async def _handle_await_slot(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str
) -> list[str]:
    options = session.temp_data.get("slot_options", [])
    idx = _parse_index(text, len(options))
    if idx is None:
        return ["Please reply with the number next to the time you'd like."]

    # Reassign the whole dict (not session.temp_data[key] = ...) — SQLAlchemy
    # only flags a JSON/JSONB column as dirty on attribute reassignment, not
    # on in-place mutation of the dict object it currently holds. Mutating
    # in place here would silently fail to persist, and the next message
    # would find `chosen_slot` missing after reloading the session.
    session.temp_data = {**session.temp_data, "chosen_slot": options[idx]}
    session.current_step = "AWAIT_NAME"
    return ["What name should this booking be under?"]


async def _handle_await_name(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str
) -> list[str]:
    name = text.strip()
    if not name:
        return ["Please send a name for the booking."]

    from datetime import datetime

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
        f"Booked! {data['service_name']} on {local_time.strftime('%a %d %b, %I:%M %p')}.\n"
        f"Reference: {appointment.booking_ref}\n"
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
