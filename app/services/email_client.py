"""Sends appointment booked/cancelled confirmation emails to the *customer*
(Appointment.customer_email), via one platform-wide Gmail mailbox using
OAuth2 (not per-tenant SMTP credentials — every tenant's customer emails go
out from the same `settings.gmail_sender_email` mailbox).

Deliberately dependency-light: talks to Google's OAuth token endpoint and
the Gmail API directly over httpx (same pattern as whatsapp_client.py /
telegram_client.py) instead of pulling in google-api-python-client.

Callers should treat every function here as best-effort: a customer not
getting a confirmation email is never a reason to fail or roll back a
booking. `notify_appointment` never raises — it logs and returns on any
failure (missing config, tenant opted out, no customer email, network/API
error) so call sites can invoke it fire-and-forget, straight after a
successful commit.
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
    return bool(
        settings.gmail_client_id
        and settings.gmail_client_secret
        and settings.gmail_refresh_token
        and settings.gmail_sender_email
    )


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
    """Fire-and-forget booked/cancelled email to the customer.

    `event` is "booked" or "cancelled". Silently does nothing (just an
    info-level log) when: the platform hasn't configured Gmail, the tenant
    hasn't turned on email_notifications_enabled, or this particular
    appointment has no customer_email on file. Any actual send failure is
    logged at warning level and swallowed — never raised.
    """
    if not is_configured():
        logger.info("Skipping %s email: Gmail OAuth2 not configured", event)
        return
    if not tenant.email_notifications_enabled:
        return
    if not appointment.customer_email:
        return

    try:
        tz = ZoneInfo(tenant.timezone)
        local_time = appointment.scheduled_at.astimezone(tz)
        when = local_time.strftime("%A, %d %B %Y at %I:%M %p")

        if event == "booked":
            subject = f"Booking confirmed — {tenant.name} ({appointment.booking_ref})"
            heading = "Your appointment is confirmed"
            intro = "Thanks for booking with us — here are your appointment details:"
        elif event == "cancelled":
            subject = f"Booking cancelled — {tenant.name} ({appointment.booking_ref})"
            heading = "Your appointment has been cancelled"
            intro = "This is to confirm that the following appointment has been cancelled:"
        else:
            logger.warning("notify_appointment called with unknown event %r", event)
            return

        html_body = _render_email(
            heading=heading,
            intro=intro,
            tenant_name=tenant.name,
            customer_name=appointment.customer_name,
            when=when,
            booking_ref=appointment.booking_ref,
        )
        await send_email(to=appointment.customer_email, subject=subject, html_body=html_body)
    except Exception:
        # Never let an email failure surface to the booking flow — the
        # appointment itself is already committed by the time this runs.
        logger.warning(
            "Failed to send %s email for appointment %s", event, appointment.booking_ref,
            exc_info=True,
        )


def _render_email(
    *, heading: str, intro: str, tenant_name: str, customer_name: str, when: str, booking_ref: str
) -> str:
    return f"""\
<div style="font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; max-width: 480px; margin: 0 auto; color: #1a1a1a;">
  <h2 style="margin-bottom: 4px;">{heading}</h2>
  <p style="color: #444;">{intro}</p>
  <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
    <tr>
      <td style="padding: 6px 0; color: #666;">Business</td>
      <td style="padding: 6px 0; font-weight: 600; text-align: right;">{tenant_name}</td>
    </tr>
    <tr>
      <td style="padding: 6px 0; color: #666;">Name</td>
      <td style="padding: 6px 0; font-weight: 600; text-align: right;">{customer_name}</td>
    </tr>
    <tr>
      <td style="padding: 6px 0; color: #666;">Date &amp; time</td>
      <td style="padding: 6px 0; font-weight: 600; text-align: right;">{when}</td>
    </tr>
    <tr>
      <td style="padding: 6px 0; color: #666;">Reference</td>
      <td style="padding: 6px 0; font-weight: 600; text-align: right;">{booking_ref}</td>
    </tr>
  </table>
  <p style="color: #888; font-size: 12px;">This is an automated notification — please don't reply to this email.</p>
</div>
"""


class EmailSendError(Exception):
    pass
