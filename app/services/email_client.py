"""Notifies the *business* (Tenant.email) whenever an appointment is booked
or cancelled — whether a customer did it themselves (public booking page,
WhatsApp, Telegram) or the tenant's own staff/admin entered it from the
dashboard. Staff often take bookings on a customer's behalf (phone calls,
walk-ins), so the business owner still wants the heads-up either way.

Sent from one platform-wide Gmail mailbox using OAuth2 (not per-tenant SMTP
credentials — every tenant's notification goes out from the same
`settings.gmail_sender_email` mailbox, to that tenant's own `tenant.email`).

Deliberately dependency-light: talks to Google's OAuth token endpoint and
the Gmail API directly over httpx (same pattern as whatsapp_client.py /
telegram_client.py) instead of pulling in google-api-python-client.

Callers should treat every function here as best-effort: the business not
getting a notification email is never a reason to fail or roll back a
booking. `notify_appointment` never raises — it logs and returns on any
failure (missing config, tenant opted out, network/API error) so call
sites can invoke it fire-and-forget, straight after a successful commit.
"""

import base64
import logging
import time
from email.mime.text import MIMEText
from email.utils import formataddr
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.models.appointment import Appointment
from app.models.tenant import Tenant

logger = logging.getLogger("app.email")

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# Module-level cache for the short-lived Gmail API access token, refreshed
# from the long-lived refresh token as needed. A plain module global is fine
# here: worst case under concurrent requests is a couple of redundant token
# refreshes, not incorrect behaviour.
_cached_access_token: str | None = None
_cached_expires_at: float = 0.0


def is_configured() -> bool:
    """Whether the platform has Gmail OAuth2 credentials at all. Tenants
    that opt in but find this false are effectively "coming soon" — the
    admin/ops side (not the tenant) needs to finish setup."""
    return not missing_config()


def missing_config() -> list[str]:
    """Names of whichever GMAIL_* env vars are still unset — used to give a
    specific, actionable error instead of a blanket "not configured"."""
    required = {
        "GMAIL_CLIENT_ID": settings.gmail_client_id,
        "GMAIL_CLIENT_SECRET": settings.gmail_client_secret,
        "GMAIL_REFRESH_TOKEN": settings.gmail_refresh_token,
        "GMAIL_SENDER_EMAIL": settings.gmail_sender_email,
    }
    return [name for name, value in required.items() if not value]


async def _get_access_token() -> str:
    global _cached_access_token, _cached_expires_at

    # 60s safety margin so a token that's about to expire mid-request isn't
    # handed out as still-valid.
    if _cached_access_token and time.monotonic() < _cached_expires_at - 60:
        return _cached_access_token

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.gmail_client_id,
                "client_secret": settings.gmail_client_secret,
                "refresh_token": settings.gmail_refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code >= 400:
        raise EmailSendError(f"Gmail token refresh failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    _cached_access_token = data["access_token"]
    _cached_expires_at = time.monotonic() + data.get("expires_in", 3600)
    return _cached_access_token


async def send_email(*, to: str, subject: str, html_body: str) -> dict:
    """Sends one email via the Gmail API, authenticated as
    `settings.gmail_sender_email`. Raises EmailSendError on failure —
    callers that want "never raise" semantics should use
    `notify_appointment` below instead of calling this directly."""
    if not is_configured():
        raise EmailSendError("Gmail OAuth2 is not configured (GMAIL_* env vars)")

    access_token = await _get_access_token()

    message = MIMEText(html_body, "html", "utf-8")
    message["To"] = to
    message["From"] = formataddr((settings.gmail_sender_name, settings.gmail_sender_email))
    message["Subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(SEND_URL, json={"raw": raw}, headers=headers)
    if resp.status_code >= 400:
        raise EmailSendError(f"Gmail send failed ({resp.status_code}): {resp.text}")
    return resp.json()


async def notify_appointment(
    *, appointment: Appointment, tenant: Tenant, event: str
) -> None:
    """Fire-and-forget booked/cancelled email to the business (tenant.email).

    `event` is "booked" or "cancelled". Silently does nothing (just an
    info-level log) when: the platform hasn't configured Gmail, or the
    tenant hasn't turned on email_notifications_enabled. Any actual send
    failure is logged at warning level and swallowed — never raised.

    Called for every booking/cancellation regardless of who made it —
    customer (public page/WhatsApp/Telegram) or the tenant's own staff from
    the admin dashboard.
    """
    if not is_configured():
        logger.info("Skipping %s email: Gmail OAuth2 not configured", event)
        return
    if not tenant.email_notifications_enabled:
        return

    try:
        tz = ZoneInfo(tenant.timezone)
        local_time = appointment.scheduled_at.astimezone(tz)
        when = local_time.strftime("%A, %d %B %Y at %I:%M %p")

        if event == "booked":
            subject = f"New appointment booked — {appointment.customer_name} ({appointment.booking_ref})"
            heading = "New appointment scheduled"
            intro = "A customer has just booked an appointment with you:"
        elif event == "cancelled":
            subject = f"Appointment cancelled — {appointment.customer_name} ({appointment.booking_ref})"
            heading = "Appointment cancelled"
            intro = "A customer has just cancelled their appointment with you:"
        else:
            logger.warning("notify_appointment called with unknown event %r", event)
            return

        html_body = _render_email(
            heading=heading,
            intro=intro,
            customer_name=appointment.customer_name,
            customer_phone=appointment.customer_phone,
            customer_email=appointment.customer_email,
            when=when,
            booking_ref=appointment.booking_ref,
        )
        await send_email(to=tenant.email, subject=subject, html_body=html_body)
    except Exception:
        # Never let an email failure surface to the booking flow — the
        # appointment itself is already committed by the time this runs.
        logger.warning(
            "Failed to send %s email for appointment %s", event, appointment.booking_ref,
            exc_info=True,
        )


def _render_email(
    *,
    heading: str,
    intro: str,
    customer_name: str,
    customer_phone: str,
    customer_email: str | None,
    when: str,
    booking_ref: str,
) -> str:
    rows = [
        ("Customer", customer_name),
        ("Phone", customer_phone),
    ]
    if customer_email:
        rows.append(("Email", customer_email))
    rows += [
        ("Date &amp; time", when),
        ("Reference", booking_ref),
    ]
    rows_html = "".join(
        f'<tr><td style="padding: 6px 0; color: #666;">{label}</td>'
        f'<td style="padding: 6px 0; font-weight: 600; text-align: right;">{value}</td></tr>'
        for label, value in rows
    )
    return f"""\
<div style="font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; max-width: 480px; margin: 0 auto; color: #1a1a1a;">
  {_brand_header_html()}
  <h2 style="margin-bottom: 4px;">{heading}</h2>
  <p style="color: #444;">{intro}</p>
  <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
    {rows_html}
  </table>
  <p style="color: #888; font-size: 12px;">This is an automated notification from your ScheduleMate admin dashboard — please don't reply to this email.</p>
</div>
"""


def _brand_header_html() -> str:
    """Mirrors the public booking page's masthead (app/templates/base.html /
    app.css .brand / .brand-word): the schedulemate-icon.png mark next to a
    two-tone "ScheduleMate" wordmark (navy "Schedule" + green "Mate"),
    same hex values as --brand-navy / --brand-green in app.css.

    The icon image only renders when settings.public_base_url is set —
    email clients need a real public HTTPS URL, they can't load
    /static/img/... relative to nothing. The colored wordmark text always
    renders regardless (no image dependency, so it survives images-off
    email clients and a missing PUBLIC_BASE_URL alike), so the header is
    never just a blank gap when the icon can't be resolved.
    """
    logo_img = ""
    if settings.public_base_url:
        logo_url = f"{settings.public_base_url.rstrip('/')}/static/img/schedulemate-icon.png"
        logo_img = (
            f'<img src="{logo_url}" alt="" width="28" height="28" '
            f'style="vertical-align: middle; margin-right: 8px; border: 0; '
            f'display: inline-block;" />'
        )
    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 20px;">
  <tr>
    <td style="padding-bottom: 14px; border-bottom: 2px solid #0d2e6d;">
      {logo_img}<span style="font-family: Georgia, 'Times New Roman', serif; font-size: 20px; font-weight: 700; letter-spacing: -0.01em; vertical-align: middle;"><span style="color: #0d2e6d;">Schedule</span><span style="color: #0f9d63;">Mate</span></span>
    </td>
  </tr>
</table>"""


class EmailSendError(Exception):
    pass
