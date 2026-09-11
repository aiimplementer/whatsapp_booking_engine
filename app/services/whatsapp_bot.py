"""
Enhanced WhatsApp conversational booking flow with interactive date/time selection.

Each customer (tenant_id, customer_phone) has one `whatsapp_sessions` row
that tracks where they are in the conversation (`current_step`) and any
selections made so far (`temp_data`). This module is pure logic — it reads/
mutates the given session object and returns the list of replies to send;
the router is responsible for persisting the session and actually calling
the Cloud API to send those replies.

Replies are either:
  - a plain str -> sent as a text message, or
  - a dict {"type": "interactive_list", "body_text", "button_text", "sections"}
    -> sent as a WhatsApp interactive list message (see whatsapp_client.send_interactive_list)

Every list-driven step also accepts the old plain-text numeric reply (e.g. "1",
"2", ...) as a fallback, since not every WhatsApp client renders lists the
same way and customers sometimes just type the number they see.

Steps: MAIN_MENU -> AWAIT_SERVICE -> AWAIT_DATE -> AWAIT_TIME -> AWAIT_NAME -> (booked, back to MAIN_MENU)

Features:
- 7-day date grouping with forward/backward navigation
- Time slots dynamically retrieved for each selected date
- Customer name included in confirmation message
"""

import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.scheduling import Service
from app.models.tenant import Tenant
from app.models.whatsapp_session import WhatsAppSession
from app.services.slots import compute_available_slots

# WhatsApp interactive lists cap out at 10 total rows across all sections.
MAX_LIST_ROWS = 10
# Row/section title limit enforced by the Cloud API.
ROW_TITLE_MAX = 24


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "\u2026"


def _generate_booking_ref() -> str:
    return f"APT-{secrets.token_hex(4).upper()}"


def _reset(session: WhatsAppSession) -> None:
    session.current_step = "MAIN_MENU"
    session.temp_data = {}


def _choice_index(text: str, list_reply_id: str | None, prefix: str) -> int | None:
    """Resolve a selection to a zero-based index, from either a tapped list
    row (id like '{prefix}{n}') or a typed number (1-based, like the labels
    shown to the customer)."""
    if list_reply_id is not None:
        if list_reply_id.startswith(prefix):
            suffix = list_reply_id[len(prefix):]
            if suffix.isdigit():
                return int(suffix)
        return None
    text = text.strip()
    if text.isdigit():
        return int(text) - 1
    return None


def _render_main_menu() -> dict:
    return {
        "type": "interactive_list",
        "body_text": "Hi! I can help you book an appointment. Tap below to get started, or reply *menu* any time.",
        "button_text": "Menu",
        "sections": [
            {
                "title": "Options",
                "rows": [
                    {"id": "menu_book", "title": "Book an appointment"},
                    {
                        "id": "menu_check",
                        "title": "Check a booking",
                        "description": "Look up by reference number",
                    },
                    {
                        "id": "menu_cancel",
                        "title": "Cancel a booking",
                        "description": "Cancel by reference number",
                    },
                ],
            }
        ],
    }


async def handle_incoming_message(
    db: AsyncSession,
    tenant: Tenant,
    session: WhatsAppSession,
    text: str,
    list_reply_id: str | None = None,
) -> list:
    text = (text or "").strip()
    lowered = text.lower()

    # A typed reset command always takes priority. List taps never produce
    # these words, so this only fires for actual typed text.
    if list_reply_id is None and lowered in ("menu", "hi", "hello", "start"):
        _reset(session)
        return [_render_main_menu()]

    if session.current_step == "MAIN_MENU":
        return await _handle_main_menu(db, tenant, session, text, lowered, list_reply_id)
    if session.current_step == "AWAIT_SERVICE":
        return await _handle_await_service(db, tenant, session, text, list_reply_id)
    if session.current_step == "AWAIT_DATE":
        return await _handle_await_date(db, tenant, session, text, list_reply_id)
    if session.current_step == "AWAIT_TIME":
        return await _handle_await_time(db, tenant, session, text, list_reply_id)
    if session.current_step == "AWAIT_NAME":
        return await _handle_await_name(db, tenant, session, text)

    # Unknown/stale step — don't get the customer stuck.
    _reset(session)
    return [_render_main_menu()]


async def _handle_main_menu(
    db: AsyncSession,
    tenant: Tenant,
    session: WhatsAppSession,
    text: str,
    lowered: str,
    list_reply_id: str | None,
) -> list:
    chose_book = list_reply_id == "menu_book" or lowered == "1" or "book" in lowered
    chose_check = (
        list_reply_id == "menu_check"
        or lowered == "2"
        or (list_reply_id is None and lowered.startswith("apt-"))
        or (list_reply_id is None and "cancel" not in lowered and "check" in lowered)
    )
    chose_cancel = list_reply_id == "menu_cancel" or lowered == "3" or "cancel" in lowered

    if chose_book:
        result = await db.execute(
            select(Service).where(Service.tenant_id == tenant.id, Service.active.is_(True))
        )
        services = result.scalars().all()
        if not services:
            return ["Sorry, no services are available for booking right now."]

        service_ids = [str(s.id) for s in services]
        service_names = [s.name for s in services]
        service_durations = [s.duration_minutes for s in services]

        session.current_step = "AWAIT_SERVICE"
        session.temp_data = {
            "service_ids": service_ids,
            "service_names": service_names,
            "service_durations": service_durations,
        }
        return [_render_service_list(service_names, service_durations)]

    if chose_check or chose_cancel:
        # List taps never carry a booking reference — only a typed message can.
        booking_ref = text_upper_if_ref(lowered) if list_reply_id is None else None
        if not booking_ref:
            session.current_step = "MAIN_MENU"
            session.temp_data = {"pending_ref_action": "cancel" if chose_cancel else "check"}
            verb = "cancel" if chose_cancel else "look up"
            return [f"Please send the booking reference you'd like to {verb}, e.g. APT-A1B2C3D4."]
        # A typed reference on its own (no menu tap first) defaults to a
        # lookup unless a pending "cancel" request is waiting on this ref.
        # Don't clear pending_ref_action here — if the ref turns out not to
        # match anything, _lookup_booking/_cancel_booking leave temp_data
        # untouched so a retry with the correct ref still remembers "cancel".
        action = session.temp_data.get("pending_ref_action") or ("cancel" if chose_cancel else "check")
        if action == "cancel":
            return await _cancel_booking(db, tenant, session, booking_ref)
        return await _lookup_booking(db, tenant, session, booking_ref)

    return [_render_main_menu()]


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
    if appointment is None:
        # Leave temp_data (pending_ref_action) alone so a retry with the
        # correct reference still remembers what the customer was doing.
        return ["I couldn't find a booking with that reference on this number. Please double-check and resend it, or reply *menu* to start over."]
    _reset(session)
    tz = ZoneInfo(tenant.timezone)
    local_time = appointment.scheduled_at.astimezone(tz)
    return [
        f"Booking {appointment.booking_ref}: {appointment.status}\n"
        f"Name: {appointment.customer_name}\n"
        f"{local_time.strftime('%a %d %b, %I:%M %p')}"
    ]


# Mirrors the state machine in app/schemas/appointment.py's ALLOWED_TRANSITIONS
# and the same check in the public /appointments/{ref}/cancel endpoint — a
# booking can only be cancelled while it's still PENDING or CONFIRMED.
CANCELLABLE_STATUSES = ("PENDING", "CONFIRMED")


async def _cancel_booking(
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
    if appointment is None:
        # Same as above — keep pending_ref_action so a corrected reference
        # on the next message is still treated as a cancel request.
        return ["I couldn't find a booking with that reference on this number. Please double-check and resend it, or reply *menu* to start over."]
    if appointment.status not in CANCELLABLE_STATUSES:
        _reset(session)
        return [
            f"Booking {appointment.booking_ref} is already {appointment.status} "
            f"and can't be cancelled. Reply *menu* for other options."
        ]
    appointment.status = "CANCELLED"
    _reset(session)
    tz = ZoneInfo(tenant.timezone)
    local_time = appointment.scheduled_at.astimezone(tz)
    return [
        f"Cancelled booking {appointment.booking_ref} "
        f"({local_time.strftime('%a %d %b, %I:%M %p')}).\n\n"
        f"Reply *menu* for anything else."
    ]


def _render_service_list(service_names: list[str], service_durations: list[int]) -> dict:
    shown = list(zip(service_names, service_durations))[:MAX_LIST_ROWS]
    rows = [
        {
            "id": f"svc_{i}",
            "title": _truncate(name, ROW_TITLE_MAX),
            "description": f"{duration} min",
        }
        for i, (name, duration) in enumerate(shown)
    ]
    body = "Which service would you like?"
    if len(service_names) > MAX_LIST_ROWS:
        body += f" (showing first {MAX_LIST_ROWS})"
    return {
        "type": "interactive_list",
        "body_text": body,
        "button_text": "Select service",
        "sections": [{"title": "Services", "rows": rows}],
    }


async def _handle_await_service(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str, list_reply_id: str | None
) -> list:
    service_ids = session.temp_data.get("service_ids", [])
    service_names = session.temp_data.get("service_names", [])
    service_durations = session.temp_data.get("service_durations", [])

    idx = _choice_index(text, list_reply_id, "svc_")
    if idx is None or not (0 <= idx < len(service_ids)):
        return [
            "Please select a service from the list.",
            _render_service_list(service_names, service_durations),
        ]

    service_id = service_ids[idx]
    service = await db.get(Service, service_id)

    slots = await compute_available_slots(
        db, tenant_id=tenant.id, tenant_timezone=tenant.timezone,
        service_id=service.id, min_lead_minutes=15,
    )

    if not slots:
        _reset(session)
        return ["No upcoming slots are available for that service right now. Reply *menu* to start over."]

    session.current_step = "AWAIT_DATE"
    session.temp_data = {
        "service_id": str(service.id),
        "service_name": service.name,
        "duration_minutes": service.duration_minutes,
        "all_slots": [s.start.isoformat() for s in slots],
        "date_page": 0,
    }

    return [_render_date_page(tenant.timezone, session.temp_data)]


def _unique_dates(all_slots: list[str], timezone: str) -> list:
    tz = ZoneInfo(timezone)
    unique_dates = []
    seen_dates = set()
    for slot_iso in all_slots:
        slot_dt = datetime.fromisoformat(slot_iso).astimezone(tz)
        slot_date = slot_dt.date()
        if slot_date not in seen_dates:
            unique_dates.append(slot_date)
            seen_dates.add(slot_date)
    return unique_dates


def _render_date_page(timezone: str, temp_data: dict) -> dict:
    """Render a 7-day date page (as an interactive list) with pagination rows."""
    all_slots = temp_data.get("all_slots", [])
    date_page = temp_data.get("date_page", 0)

    unique_dates = _unique_dates(all_slots, timezone)

    start_idx = date_page * 7
    end_idx = start_idx + 7
    page_dates = unique_dates[start_idx:end_idx]

    if not page_dates:
        return {
            "type": "interactive_list",
            "body_text": "No more dates available. Reply *menu* to start over.",
            "button_text": "Menu",
            "sections": [{"title": "Options", "rows": [{"id": "menu_book", "title": "Book an appointment"}]}],
        }

    rows = [
        {"id": f"date_{i}", "title": d.strftime("%a, %d-%b-%y")}
        for i, d in enumerate(page_dates)
    ]

    has_prev = date_page > 0
    has_next = end_idx < len(unique_dates)
    if has_prev:
        rows.append({"id": "date_prev", "title": "\u25c0 Previous 7 days"})
    if has_next:
        rows.append({"id": "date_next", "title": "Next 7 days \u25b6"})

    return {
        "type": "interactive_list",
        "body_text": "Which day is the appointment on?",
        "button_text": "Select date",
        "sections": [{"title": "Dates", "rows": rows}],
    }


async def _handle_await_date(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str, list_reply_id: str | None
) -> list:
    text = text.strip()
    temp_data = session.temp_data
    date_page = temp_data.get("date_page", 0)
    all_slots = temp_data.get("all_slots", [])
    unique_dates = _unique_dates(all_slots, tenant.timezone)

    start_idx = date_page * 7
    end_idx = start_idx + 7
    page_dates = unique_dates[start_idx:end_idx]

    # Pagination: either a tapped nav row, or the old typed "0" / "next number" convention.
    went_prev = list_reply_id == "date_prev" or (list_reply_id is None and text == "0")
    went_next = list_reply_id == "date_next" or (
        list_reply_id is None and text == str(len(page_dates) + 1)
    )

    if went_prev:
        if date_page > 0:
            # Reassign (not mutate-in-place) so SQLAlchemy detects the change
            # on this JSONB column and actually persists the new page.
            session.temp_data = {**temp_data, "date_page": date_page - 1}
            return [_render_date_page(tenant.timezone, session.temp_data)]
        return ["You're already on the first page. Please select a date."]

    if went_next:
        if end_idx < len(unique_dates):
            session.temp_data = {**temp_data, "date_page": date_page + 1}
            return [_render_date_page(tenant.timezone, session.temp_data)]
        return ["No more dates available."]

    idx = _choice_index(text, list_reply_id, "date_")
    if idx is None or not (0 <= idx < len(page_dates)):
        return ["Please select a date from the list.", _render_date_page(tenant.timezone, temp_data)]

    selected_date = page_dates[idx]

    tz = ZoneInfo(tenant.timezone)
    selected_slots = []
    for slot_iso in all_slots:
        slot_dt = datetime.fromisoformat(slot_iso).astimezone(tz)
        if slot_dt.date() == selected_date:
            selected_slots.append(slot_dt)

    if not selected_slots:
        return [
            "No time slots available for that date. Please pick another date.",
            _render_date_page(tenant.timezone, temp_data),
        ]

    selected_slots.sort(key=lambda x: x.time())

    session.current_step = "AWAIT_TIME"
    session.temp_data = {
        **temp_data,
        "selected_date": selected_date.isoformat(),
        "time_slots": [s.isoformat() for s in selected_slots],
    }

    return [_render_time_list(selected_date, selected_slots)]


def _render_time_list(selected_date, selected_slots: list) -> dict:
    shown = selected_slots[:MAX_LIST_ROWS]
    rows = [
        {"id": f"time_{i}", "title": slot_dt.strftime("%I:%M %p").lstrip("0")}
        for i, slot_dt in enumerate(shown)
    ]
    date_display = selected_date.strftime("%a, %d-%b-%y")
    body = f"What time on {date_display}?"
    if len(selected_slots) > MAX_LIST_ROWS:
        body += f" (showing first {MAX_LIST_ROWS})"
    return {
        "type": "interactive_list",
        "body_text": body,
        "button_text": "Select time",
        "sections": [{"title": "Times", "rows": rows}],
    }


async def _handle_await_time(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str, list_reply_id: str | None
) -> list:
    time_slots = session.temp_data.get("time_slots", [])
    idx = _choice_index(text, list_reply_id, "time_")
    if idx is None or not (0 <= idx < len(time_slots)):
        selected_date_iso = session.temp_data.get("selected_date")
        retry: list = ["Please select a time from the list."]
        if selected_date_iso:
            selected_date = datetime.fromisoformat(selected_date_iso).date()
            selected_slots = [datetime.fromisoformat(s) for s in time_slots]
            retry.append(_render_time_list(selected_date, selected_slots))
        return retry

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
        f"\u2713 Confirmed!\n\n"
        f"Name: {name}\n"
        f"Service: {data['service_name']}\n"
        f"Date & Time: {local_time.strftime('%a, %d %b at %I:%M %p')}\n"
        f"Reference: {appointment.booking_ref}\n\n"
        f"Reply *menu* for anything else."
    )
    _reset(session)
    return [confirmation]