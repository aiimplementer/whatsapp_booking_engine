"""
Thin wrapper around Meta's WhatsApp Cloud API (Graph API).

Multi-tenant routing: each tenant's `tenants.whatsapp_number` column stores
that tenant's Meta *phone_number_id* (the numeric ID Meta assigns to a
WhatsApp sender number under your Meta App) — NOT the human-readable phone
number. Incoming webhook payloads carry this same phone_number_id in
`metadata.phone_number_id`, so it's what we match on to find the right
tenant, and it's also what we send FROM.
"""

import hashlib
import hmac

import httpx

from app.config import settings

GRAPH_BASE = "https://graph.facebook.com"


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Validate Meta's X-Hub-Signature-256 header against our App Secret.

    Meta signs every webhook POST with HMAC-SHA256 of the raw request body,
    keyed by the App Secret, and sends it as `sha256=<hex>`. Recomputing and
    comparing (with hmac.compare_digest, to avoid timing attacks) is the only
    way to know a webhook call actually came from Meta and not a forged
    request hitting our public URL.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    if not settings.whatsapp_app_secret:
        # No secret configured (e.g. local dev) — nothing to check against.
        # Never treat this as "valid"; caller should reject instead.
        return False
    expected = hmac.new(
        settings.whatsapp_app_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


async def send_text_message(*, phone_number_id: str, to: str, body: str) -> dict:
    """Send a plain-text WhatsApp message via the Cloud API.

    `to` must be in international format without a leading '+' (e.g.
    "919876543210") — this is what Cloud API expects and what it echoes back
    in incoming webhook payloads' `messages[].from`.
    """
    url = f"{GRAPH_BASE}/{settings.whatsapp_api_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code >= 400:
        # Surface Meta's error body (invalid token, unregistered number,
        # 24h-session-window closed, etc.) rather than a bare status code —
        # this is almost always what you need to see to fix a send failure.
        raise WhatsAppSendError(resp.status_code, resp.text)
    return resp.json()


class WhatsAppSendError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"WhatsApp send failed ({status_code}): {body}")
