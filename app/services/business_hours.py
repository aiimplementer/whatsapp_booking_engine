"""Shared helpers for rendering a tenant's working hours (as configured on
the admin dashboard's Scheduling > Working hours tab, i.e. the tenant-wide
`SchedulingConfig` row where service_id IS NULL) for display to customers.

Used by both app/services/whatsapp_bot.py (chat-style text) and
app/routers/public.py (structured JSON for the online booking page), so
the two surfaces stay consistent instead of maintaining separate
day/time-formatting logic that could drift apart.
"""
from datetime import datetime

from app.models.scheduling import SchedulingConfig

# Bit0=Sun .. bit6=Sat, matching the working_days bitmask convention used by
# SchedulingConfig / TimeSlotWindow (see app/schemas/scheduling.py).
DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def format_time_12h(hhmm: str) -> str:
    """'09:00' -> '9:00 AM'."""
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")


def working_hours_by_day(config: SchedulingConfig) -> list[dict]:
    """Returns one entry per day of the week (Sun..Sat), each:
    {"day": "Mon", "is_open": bool, "windows": ["9:00 AM \u2013 5:00 PM", ...]}
    `windows` is always empty for closed days, and may also be empty for an
    open day that has no time windows configured yet.
    """
    windows_by_day: dict[int, list[tuple[str, str]]] = {}
    for window in config.time_slots or []:
        windows_by_day.setdefault(window["day"], []).append((window["start"], window["end"]))

    entries = []
    for day in range(7):
        is_open = bool(config.working_days & (1 << day))
        windows = sorted(windows_by_day.get(day, [])) if is_open else []
        entries.append(
            {
                "day": DAY_NAMES[day],
                "is_open": is_open,
                "windows": [
                    f"{format_time_12h(start)} \u2013 {format_time_12h(end)}"
                    for start, end in windows
                ],
            }
        )
    return entries


def format_working_hours_text(config: SchedulingConfig) -> str:
    """Chat-friendly plain-text rendering, one line per day."""
    lines = []
    for entry in working_hours_by_day(config):
        if not entry["is_open"]:
            lines.append(f"{entry['day']}: Closed")
        elif not entry["windows"]:
            lines.append(f"{entry['day']}: Hours not set")
        else:
            lines.append(f"{entry['day']}: {', '.join(entry['windows'])}")
    return "\n".join(lines)
