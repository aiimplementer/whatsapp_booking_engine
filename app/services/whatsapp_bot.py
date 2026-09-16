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
    -> sent as a WhatsApp interactive list message (see whatsapp_client.send_interactive_list), or
  - a dict {"type": "request_contact", "body_text", "button_text"}
    -> Telegram-only: rendered as a native "share contact" keyboard button
       (see telegram_client.send_contact_request). No WhatsApp equivalent
       exists, so this is never produced for a WhatsAppSession.

Every list-driven step also accepts the old plain-text numeric reply (e.g. "1",
"2", ...) as a fallback, since not every WhatsApp client renders lists the
same way and customers sometimes just type the number they see.

Steps: MAIN_MENU -> AWAIT_SERVICE -> AWAIT_DATE -> AWAIT_TIME -> AWAIT_NAME ->
    [AWAIT_CONTACT, Telegram sessions only] -> (booked, back to MAIN_MENU)

AWAIT_CONTACT only ever runs for Telegram: WhatsApp's `customer_phone` is
already the customer's real number, so a WhatsAppSession finalizes the
booking straight after AWAIT_NAME, exactly as before. A Telegram session is
detected by duck-typing on `customer_chat_id` (an attribute only
TelegramSession has) rather than importing that model here, so this file
stays channel-agnostic and nothing changes for WhatsApp. Once a Telegram
customer shares their contact in AWAIT_CONTACT, that verified number is
what gets written to `customer_phone` on the appointment — the same column
WhatsApp uses — so both channels store the same kind of value there.

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
from app.models.scheduling import SchedulingConfig, Service
from app.models.tenant import Tenant
from app.models.whatsapp_session import WhatsAppSession
from app.services.business_hours import format_working_hours_text
from app.services.slots import compute_available_slots

# WhatsApp interactive lists cap out at 10 total rows across all sections.
MAX_LIST_ROWS = 8
# Row/section title limit enforced by the Cloud API.
ROW_TITLE_MAX = 24


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "\u2026"


def _generate_booking_ref() -> str:
    return f"APT-{secrets.token_hex(4).upper()}"


def _reset(session: WhatsAppSession) -> None:
    session.current_step = "MAIN_MENU"
    session.temp_data = {}


def _channel_requires_contact_share(session: WhatsAppSession) -> bool:
    """True only for Telegram sessions.

    Detected by duck-typing on `customer_chat_id` — an attribute only
    TelegramSession defines (see its docstring) — rather than importing
    TelegramSession here, so this module never gains a WhatsApp-specific or
    Telegram-specific import and stays a single shared state machine. A
    WhatsAppSession never has this attribute, so this always returns False
    for it and the WhatsApp flow is completely unaffected.
    """
    return hasattr(session, "customer_chat_id")


def _render_contact_request() -> dict:
    return {
        "type": "request_contact",
        "body_text": (
            "One last thing \u2014 please share your contact number so we can "
            "confirm your appointment. Tap the button below."
        ),
        "button_text": "\U0001f4de Share Contact",
    }


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


def _render_main_menu(tenant_name: str) -> dict:
    return {
        "type": "interactive_list",
        "body_text": (
            f"Welcome to *{tenant_name}*!\n\n"
            "Your personal appointment assistant, available anytime. Please select an option from the menu below to continue."
        ),
        "button_text": "Explore",
        "sections": [
            {
                "title": "Choose an Option",
                "rows": [
                    {"id": "menu_book", "title": "\U0001f4c5 Schedule Appointment"},
                    {
                        "id": "menu_check",
                        "title": "\U0001f9fe View Booking Details",
                        "description": "View by reference number",
                    },
                    {
                        "id": "menu_cancel",
                        "title": "\u274c Cancel a Booking",
                        "description": "Cancel by reference number",
                    },
                    {
                        "id": "menu_offers",
                        "title": "\U0001f381 Offers",
                        "description": "View current offers & promotions",
                    },
                    {
                        "id": "menu_announcements",
                        "title": "\U0001f4e2 Announcements",
                        "description": "View latest business announcements",
                    },
                    {
                        "id": "menu_hours",
                        "title": "\U0001f550 Business Hours",
                        "description": "View our working hours",
                    },
                    {
                        "id": "menu_policy",
                        "title": "\U0001f4c4 Cancellation Policy",
                        "description": "View our cancellation policy",
                    },
                ],
            }
        ],
    }


# Bit0=Sun .. bit6=Sat, matching the working_days bitmask convention used by
# SchedulingConfig / TimeSlotWindow (see app/schemas/scheduling.py). Shared
# with the public booking page — see app/services/business_hours.py — so
# both surfaces render the same schedule the same way.


async def _show_business_hours(db: AsyncSession, tenant: Tenant) -> list:
    """Business-level menu option: reply with the tenant's working hours,
    sourced from the tenant-wide scheduling config (service_id IS NULL) —
    the same "Applies to: Tenant-wide default" config set on the admin
    dashboard's Scheduling > Working hours tab."""
    result = await db.execute(
        select(SchedulingConfig).where(
            SchedulingConfig.tenant_id == tenant.id,
            SchedulingConfig.service_id.is_(None),
        )
    )
    config = result.scalar_one_or_none()

    if config is None:
        return [
            f"Sorry, working hours haven't been set up for *{tenant.name}* yet. "
            "Please reach out to us directly, or reply *menu* for other options."
        ]

    body = (
        f"\U0001f550 *Working Hours \u2014 {tenant.name}*\n\n"
        f"{format_working_hours_text(config)}\n\n"
        "Reply *menu* for other options."
    )
    return [body]


def _show_cancellation_policy(tenant: Tenant) -> list:
    """Business-level menu option: reply with the tenant's cancellation
    policy, sourced from tenants.cancellation_policy — the free-text field
    set on the admin dashboard's Business settings page. No DB round-trip
    needed since `tenant` is already loaded for every incoming message."""
    policy_text = (tenant.cancellation_policy or "").strip()
    if not policy_text:
        return [
            f"*{tenant.name}* hasn't published a cancellation policy yet. "
            "Please reach out to us directly with any questions, or reply *menu* for other options."
        ]
    body = (
        f"\U0001f4c4 *Cancellation Policy \u2014 {tenant.name}*\n\n"
        f"{policy_text}\n\n"
        "Reply *menu* for other options."
    )
    return [body]


def _show_offers(tenant: Tenant) -> list:
    """Business-level menu option: reply with the tenant's current
    offers/promotions, sourced from tenants.offers — the free-text field
    set on the admin dashboard's Business settings page. Same pattern as
    _show_cancellation_policy; no DB round-trip needed since `tenant` is
    already loaded for every incoming message."""
    offers_text = (tenant.offers or "").strip()
    if not offers_text:
        return [
            f"*{tenant.name}* doesn't have any offers running right now. "
            "Reply *menu* for other options."
        ]
    body = (
        f"\U0001f381 *Offers \u2014 {tenant.name}*\n\n"
        f"{offers_text}\n\n"
        "Reply *menu* for other options."
    )
    return [body]


def _show_announcements(tenant: Tenant) -> list:
    """Business-level menu option: reply with the tenant's announcements,
    sourced from tenants.announcements — the free-text field set on the
    admin dashboard's Business settings page. Same pattern as
    _show_cancellation_policy/_show_offers; no DB round-trip needed since
    `tenant` is already loaded for every incoming message."""
    announcements_text = (tenant.announcements or "").strip()
    if not announcements_text:
        return [
            f"*{tenant.name}* doesn't have any announcements right now. "
            "Reply *menu* for other options."
        ]
    body = (
        f"\U0001f4e2 *Announcements \u2014 {tenant.name}*\n\n"
        f"{announcements_text}\n\n"
        "Reply *menu* for other options."
    )
    return [body]


async def handle_incoming_message(
    db: AsyncSession,
    tenant: Tenant,
    session: WhatsAppSession,
    text: str,
    list_reply_id: str | None = None,
    contact: dict | None = None,
) -> list:
    """`contact`, when provided, is a Telegram-only, already-verified
    `{"phone_number": ...}` payload (see routers/telegram.py) confirming the
    customer tapped the native "share contact" button. It's `None` for
    every WhatsApp call site (that router doesn't pass it) and for every
    Telegram update that isn't a shared contact, so this new parameter
    changes nothing for WhatsApp or for any Telegram step other than
    AWAIT_CONTACT.
    """
    text = (text or "").strip()
    lowered = text.lower()

    # A typed reset command always takes priority. List taps never produce
    # these words, so this only fires for actual typed text.
    if list_reply_id is None and lowered in ("menu", "hi", "hello", "start"):
        _reset(session)
        return [_render_main_menu(tenant.name)]

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
    if session.current_step == "AWAIT_CONTACT":
        return await _handle_await_contact(db, tenant, session, contact)

    # Unknown/stale step — don't get the customer stuck.
    _reset(session)
    return [_render_main_menu(tenant.name)]


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
    chose_hours = (
        list_reply_id == "menu_hours"
        or lowered == "6"
        or "hours" in lowered
        or "timing" in lowered
    )
    chose_policy = (
        list_reply_id == "menu_policy"
        or lowered == "7"
        or "policy" in lowered
        or "policies" in lowered
    )
    chose_offers = (
        list_reply_id == "menu_offers"
        or lowered == "4"
        or "offer" in lowered
        or "promo" in lowered
    )
    chose_announcements = (
        list_reply_id == "menu_announcements"
        or lowered == "5"
        or "announce" in lowered
        or "notice" in lowered
    )

    if chose_offers:
        return _show_offers(tenant)

    if chose_announcements:
        return _show_announcements(tenant)

    if chose_hours:
        return await _show_business_hours(db, tenant)

    if chose_policy:
        return _show_cancellation_policy(tenant)

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

    return [_render_main_menu(tenant.name)]


def text_upper_if_ref(lowered: str) -> str | None:
    if lowered.startswith("apt-"):
        return lowered.upper()
    return None


# Icon + display label per appointment status, for a friendlier lookup reply.
STATUS_DISPLAY = {
    "PENDING": ("\u23f3", "Pending"),
    "CONFIRMED": ("\u2705", "Confirmed"),
    "CHECKED_IN": ("\U0001f7e2", "Checked In"),
    "COMPLETED": ("\u2714\ufe0f", "Completed"),
    "CANCELLED": ("\u274c", "Cancelled"),
    "RESCHEDULED": ("\U0001f504", "Rescheduled"),
    "NO_SHOW": ("\U0001f6ab", "No Show"),
    "EXPIRED": ("\u231b", "Expired"),
}


def _format_status(status: str) -> str:
    icon, label = STATUS_DISPLAY.get(status, ("", status.title()))
    return f"{label} {icon}".strip()


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
        f"\U0001f4cb *Booking Details*\n"
        f"Reference: *{appointment.booking_ref}*\n"
        f"Status: *{_format_status(appointment.status)}*\n"
        f"Name: {appointment.customer_name}\n"
        f"Date & Time: {local_time.strftime('%a, %d %b at %I:%M %p')}"
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
        f"This is to confirm that booking *{appointment.booking_ref}*, originally "
        f"scheduled for {local_time.strftime('%a, %d %b at %I:%M %p')}, has been cancelled.\n\n"
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
    body = "Great! Which service can we set up for you today?"
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
        "time_page": 0,
    }

    return [_render_time_list(selected_date, selected_slots, time_page=0)]


def _render_time_list(selected_date, selected_slots: list, time_page: int = 0) -> dict:
    """Render a paginated time list with navigation rows."""
    start_idx = time_page * MAX_LIST_ROWS
    end_idx = start_idx + MAX_LIST_ROWS
    page_slots = selected_slots[start_idx:end_idx]
    
    rows = [
        {"id": f"time_{i}", "title": slot_dt.strftime("%I:%M %p").lstrip("0")}
        for i, slot_dt in enumerate(page_slots)
    ]
    
    # Add pagination controls
    has_prev = time_page > 0
    has_next = end_idx < len(selected_slots)
    
    if has_prev:
        rows.append({"id": "time_prev", "title": "\u25c0 Previous times"})
    if has_next:
        rows.append({"id": "time_next", "title": "More times \u25b6"})
    
    date_display = selected_date.strftime("%a, %d-%b-%y")
    body = f"What time on {date_display}?"
    
    return {
        "type": "interactive_list",
        "body_text": body,
        "button_text": "Select time",
        "sections": [{"title": "Times", "rows": rows}],
    }


async def _handle_await_time(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str, list_reply_id: str | None
) -> list:
    text = text.strip()
    temp_data = session.temp_data
    time_slots = temp_data.get("time_slots", [])
    time_page = temp_data.get("time_page", 0)
    
    selected_date_iso = temp_data.get("selected_date")
    selected_date = datetime.fromisoformat(selected_date_iso).date() if selected_date_iso else None
    selected_slots = [datetime.fromisoformat(s) for s in time_slots]
    
    start_idx = time_page * MAX_LIST_ROWS
    end_idx = start_idx + MAX_LIST_ROWS
    page_slots = selected_slots[start_idx:end_idx]
    
    # Pagination: handle previous/next navigation
    went_prev = list_reply_id == "time_prev" or (list_reply_id is None and text == "0")
    went_next = list_reply_id == "time_next" or (
        list_reply_id is None and text == str(len(page_slots) + 1)
    )
    
    if went_prev:
        if time_page > 0:
            session.temp_data = {**temp_data, "time_page": time_page - 1}
            return [_render_time_list(selected_date, selected_slots, time_page=time_page - 1)]
        return ["You're already viewing the first set of times. Please select a time."]
    
    if went_next:
        if end_idx < len(selected_slots):
            session.temp_data = {**temp_data, "time_page": time_page + 1}
            return [_render_time_list(selected_date, selected_slots, time_page=time_page + 1)]
        return ["No more times available."]
    
    # Handle regular time selection
    idx = _choice_index(text, list_reply_id, "time_")
    if idx is None or not (0 <= idx < len(page_slots)):
        retry: list = ["Please select a time from the list."]
        if selected_date:
            retry.append(_render_time_list(selected_date, selected_slots, time_page=time_page))
        return retry

    # Convert page slot index to overall slot index
    overall_idx = start_idx + idx
    chosen_slot_iso = time_slots[overall_idx]
    session.temp_data = {**session.temp_data, "chosen_slot": chosen_slot_iso}
    session.current_step = "AWAIT_NAME"
    return ["Please provide the name under which you would like the booking to be made"]


async def _handle_await_name(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, text: str
) -> list:
    name = text.strip()
    if not name:
        return ["Please send a name for the booking."]

    if _channel_requires_contact_share(session):
        # Telegram only (see _channel_requires_contact_share): hold the name
        # and ask for a verified contact number before finalizing anything.
        # WhatsApp sessions never take this branch, so their behavior below
        # (finalize immediately) is unchanged.
        session.temp_data = {**session.temp_data, "pending_name": name}
        session.current_step = "AWAIT_CONTACT"
        return [_render_contact_request()]

    return await _finalize_booking(db, tenant, session, name)


async def _handle_await_contact(
    db: AsyncSession, tenant: Tenant, session: WhatsAppSession, contact: dict | None
) -> list:
    name = session.temp_data.get("pending_name")
    if not name:
        # Stale/unexpected state — don't leave the customer stuck here.
        _reset(session)
        return [_render_main_menu(tenant.name)]

    phone = (contact or {}).get("phone_number")
    if not phone:
        # Permission denied / declined (typed a message, dismissed the
        # keyboard, or shared a contact that wasn't verified as their own —
        # see routers/telegram.py). Per requirement: never save or confirm
        # the appointment without it, and stay on this step so a retry with
        # the button still works.
        return [
            "This appointment can't be confirmed without your contact number.",
            _render_contact_request(),
        ]

    # Persist onto the session (not just temp_data, which _reset() clears)
    # so the "look up/cancel my booking" flow — which filters on
    # `Appointment.customer_phone == session.customer_phone` — keeps
    # matching this customer's bookings by their real number on every
    # future visit to this chat, not just this one. Only TelegramSession
    # ever reaches this handler (see _channel_requires_contact_share), and
    # only it defines `verified_phone`.
    session.verified_phone = phone

    return await _finalize_booking(db, tenant, session, name, contact_phone=phone)


async def _finalize_booking(
    db: AsyncSession,
    tenant: Tenant,
    session: WhatsAppSession,
    name: str,
    contact_phone: str | None = None,
) -> list[str]:
    """Creates and confirms the appointment. `contact_phone`, when given, is
    a Telegram customer's verified number (from AWAIT_CONTACT) and is what
    gets stored in `customer_phone` — the same column WhatsApp has always
    used, so a Telegram booking's `customer_phone` now holds the customer's
    real number exactly like a WhatsApp booking's does, with no separate
    column needed. (By the time this runs, `session.verified_phone` — see
    `_handle_await_contact` — already matches `contact_phone`, so
    `session.customer_phone` would resolve to the same value; passing
    `contact_phone` through explicitly just avoids relying on that
    ordering.) The reference-lookup/cancel flows above, which filter on
    `Appointment.customer_phone == session.customer_phone`, keep working
    because `TelegramSession.customer_phone` now resolves to that same
    verified number too.
    """
    data = session.temp_data
    scheduled_at = datetime.fromisoformat(data["chosen_slot"])
    appointment = Appointment(
        tenant_id=tenant.id,
        service_id=data["service_id"],
        customer_name=name,
        customer_phone=contact_phone or session.customer_phone,
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