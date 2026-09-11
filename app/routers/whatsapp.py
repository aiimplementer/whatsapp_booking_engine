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
from app.services.whatsapp_client import (
    WhatsAppSendError,
    send_text_message,
    send_interactive_list,
    verify_signature,
)

logger = logging.getLogger("app.whatsapp")

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

    # Always ack with 200 once the signature checks out
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            for message in value.get("messages", []):
                try:
                    await _process_message(phone_number_id, message)
                except Exception:
                    logger.exception("Failed to process WhatsApp message: %s", message)

    return {"status": "received"}


async def _process_message(phone_number_id: str | None, message: dict) -> None:
    """Process both text and interactive list messages."""
    if phone_number_id is None:
        return

    customer_phone = message["from"]  # Meta gives this without a leading '+'
    message_type = message.get("type")
    
    # Extract text or list_reply
    text = None
    list_reply_id = None
    
    if message_type == "text":
        text = message.get("text", {}).get("body", "")
    elif message_type == "interactive":
        interactive = message.get("interactive", {})
        if interactive.get("type") == "list_reply":
            list_reply = interactive.get("list_reply", {})
            list_reply_id = list_reply.get("id")
    else:
        # Unsupported message type (image, location, etc.)
        return
    
    # If neither text nor list_reply, can't proceed
    if not text and not list_reply_id:
        return

    # Tenant lookup
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

        # Pass both text and list_reply_id to bot
        replies = await handle_incoming_message(db, tenant, session, text or "", list_reply_id)
        await db.commit()
        break

    # Send all replies (text or interactive)
    for reply in replies:
        try:
            if isinstance(reply, dict) and reply.get("type") == "interactive_list":
                # Send interactive list message
                await send_interactive_list(
                    phone_number_id=phone_number_id,
                    to=customer_phone,
                    body_text=reply["body_text"],
                    button_text=reply["button_text"],
                    sections=reply["sections"],
                )
            else:
                # Send text message
                await send_text_message(
                    phone_number_id=phone_number_id,
                    to=customer_phone,
                    body=reply,
                )
        except WhatsAppSendError:
            logger.exception("Failed to send WhatsApp reply to %s", customer_phone)


# ---------------------------------------------------------------------------
# Admin endpoints (normal JWT auth, tenant-scoped)
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