import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, get_db_for_tenant
from app.deps import get_tenant_db, require_role
from app.models.tenant import Tenant, TenantUser
from app.models.whatsapp_session import WhatsAppSession
from app.schemas.whatsapp import WhatsAppConnectIn, WhatsAppSendIn, WhatsAppStatusOut
from app.services.whatsapp_bot import handle_incoming_message
from app.services.whatsapp_client import WhatsAppSendError, send_text_message, verify_signature

logger = logging.getLogger("app.whatsapp")

# NOTE: this router deliberately does NOT use the JWT-based
# app.deps.get_tenant_db for the webhook endpoints — Meta isn't a logged-in
# tenant user, so those two endpoints use their own auth (verify-token for
# the GET handshake, HMAC signature for every POST) instead. The admin
# endpoints (/send, /connect, /disconnect) DO use the normal JWT flow, same
# as every other authenticated endpoint in this app.
router = APIRouter(prefix="/api/v1/whatsapp", tags=["whatsapp"])


# ---------------------------------------------------------------------------
# Webhook: called by Meta, not by our own frontend
# ---------------------------------------------------------------------------

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    """One-time handshake Meta performs when you save the webhook URL in the
    App dashboard. We must echo back hub.challenge as plain text (not JSON)
    if hub.verify_token matches what we configured."""
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Verification token mismatch")


@router.post("/webhook")
async def receive_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not verify_signature(raw_body, signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    payload = await request.json()

    # Always ack with 200 once the signature checks out — Meta retries (and
    # eventually disables) a webhook that doesn't respond quickly, so any
    # per-message failure below is logged rather than turned into a non-200.
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            for message in value.get("messages", []):
                try:
                    await _process_message(phone_number_id, message)
                except Exception:
                    logger.exception("Failed to process WhatsApp message: %s", message)
            # value.get("statuses") (delivered/read receipts) is ignored —
            # nothing in this app currently needs delivery-status tracking.

    return {"status": "received"}


async def _process_message(phone_number_id: str | None, message: dict) -> None:
    if phone_number_id is None or message.get("type") != "text":
        return  # non-text messages (image, location, etc.) aren't handled yet

    customer_phone = message["from"]  # Meta gives this without a leading '+'
    text = message.get("text", {}).get("body", "")

    # Tenant lookup uses a plain, tenant-agnostic session — there is no
    # tenant context yet at this point, that's precisely what we're resolving.
    async for lookup_db in get_db():
        result = await lookup_db.execute(
            select(Tenant).where(Tenant.whatsapp_number == phone_number_id)
        )
        tenant = result.scalar_one_or_none()
        break
    if tenant is None:
        logger.warning("No tenant connected for phone_number_id=%s", phone_number_id)
        return

    async for db in get_db_for_tenant(str(tenant.id)):
        result = await db.execute(
            select(WhatsAppSession).where(
                WhatsAppSession.tenant_id == tenant.id,
                WhatsAppSession.customer_phone == customer_phone,
            )
        )
        session = result.scalar_one_or_none()
        if session is None:
            session = WhatsAppSession(tenant_id=tenant.id, customer_phone=customer_phone)
            db.add(session)

        replies = await handle_incoming_message(db, tenant, session, text)
        await db.commit()
        break

    for reply in replies:
        try:
            await send_text_message(phone_number_id=phone_number_id, to=customer_phone, body=reply)
        except WhatsAppSendError:
            logger.exception("Failed to send WhatsApp reply to %s", customer_phone)


# ---------------------------------------------------------------------------
# Admin endpoints (normal JWT auth, tenant-scoped like the rest of the app)
# ---------------------------------------------------------------------------

@router.post("/connect", response_model=WhatsAppStatusOut)
async def connect_whatsapp(
    body: WhatsAppConnectIn,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant = await db.get(Tenant, user.tenant_id)
    tenant.whatsapp_number = body.phone_number_id
    await db.commit()
    return WhatsAppStatusOut(connected=True, phone_number_id=body.phone_number_id)


@router.post("/disconnect", response_model=WhatsAppStatusOut)
async def disconnect_whatsapp(
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant = await db.get(Tenant, user.tenant_id)
    tenant.whatsapp_number = None
    await db.commit()
    return WhatsAppStatusOut(connected=False)


@router.get("/status", response_model=WhatsAppStatusOut)
async def whatsapp_status(
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant = await db.get(Tenant, user.tenant_id)
    return WhatsAppStatusOut(
        connected=tenant.whatsapp_number is not None,
        phone_number_id=tenant.whatsapp_number,
    )


@router.post("/send")
async def send_whatsapp_message(
    body: WhatsAppSendIn,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Manual/test send — useful to confirm Meta credentials work before
    relying on the bot flow, and for ad-hoc messages outside the auto-flow."""
    tenant = await db.get(Tenant, user.tenant_id)
    if not tenant.whatsapp_number:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="No WhatsApp number connected for this tenant")
    try:
        result = await send_text_message(
            phone_number_id=tenant.whatsapp_number, to=body.to, body=body.body
        )
    except WhatsAppSendError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return result
