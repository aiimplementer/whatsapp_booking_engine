"""
Telegram connector — a second messaging channel alongside WhatsApp.

This module is purely additive: a new router, new models
(telegram_configs/telegram_sessions), and a new client (telegram_client.py).
It does not import, modify, or depend on anything in routers/whatsapp.py,
services/whatsapp_client.py, or the whatsapp_sessions table.

The one thing it *does* reuse is
`app.services.whatsapp_bot.handle_incoming_message` — that function was
already written channel-agnostically (it only reads/writes
`session.current_step` / `session.temp_data`, and returns plain strings or
`{"type": "interactive_list", ...}` reply dicts; it has no WhatsApp-specific
code in it). Passing it a TelegramSession instead of a WhatsAppSession works
without touching that file at all, so the entire booking conversation flow
(menu, service/date/time pickers, confirmation) is shared between both
channels with zero duplication.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, get_db_for_tenant
from app.deps import get_tenant_db, require_role
from app.models.tenant import Tenant, TenantUser
from app.models.telegram_config import TelegramConfig
from app.models.telegram_session import TelegramSession
from app.schemas.telegram import TelegramConnectIn, TelegramSendIn, TelegramStatusOut
from app.services.whatsapp_bot import handle_incoming_message
from app.services.telegram_client import (
    TelegramSendError,
    answer_callback_query,
    delete_webhook,
    get_me,
    send_inline_keyboard,
    send_text_message,
    set_webhook,
    verify_secret_token,
)

logger = logging.getLogger("app.telegram")

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


# ---------------------------------------------------------------------------
# Webhook: called by Telegram, not by our own frontend
# ---------------------------------------------------------------------------

@router.post("/webhook/{bot_token}")
async def receive_webhook(
    request: Request,
    bot_token: str = Path(...),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not verify_secret_token(x_telegram_bot_api_secret_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid secret token")

    update = await request.json()

    try:
        await _process_update(bot_token, update)
    except Exception:
        logger.exception("Failed to process Telegram update: %s", update)

    # Telegram just needs a 200 to consider the update delivered.
    return Response(status_code=status.HTTP_200_OK)


async def _process_update(bot_token: str, update: dict) -> None:
    message = update.get("message")
    callback_query = update.get("callback_query")

    text: str | None = None
    callback_data: str | None = None
    callback_query_id: str | None = None

    if message is not None:
        chat_id = str(message.get("chat", {}).get("id"))
        text = message.get("text", "")
    elif callback_query is not None:
        chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id"))
        callback_data = callback_query.get("data")
        callback_query_id = callback_query.get("id")
    else:
        # Unsupported update type (edited_message, channel_post, etc.)
        return

    if not chat_id or chat_id == "None":
        return
    if not text and not callback_data:
        return

    # Tenant lookup by bot_token, same shape as the WhatsApp webhook's
    # lookup by phone_number_id.
    async for lookup_db in get_db():
        result = await lookup_db.execute(
            select(TelegramConfig).where(TelegramConfig.bot_token == bot_token)
        )
        config = result.scalar_one_or_none()
        break

    if config is None:
        logger.warning("No tenant connected for Telegram bot_token=%s...", bot_token[:8])
        return

    async for db in get_db_for_tenant(str(config.tenant_id)):
        tenant = await db.get(Tenant, config.tenant_id)
        if tenant is None:
            break

        # Same FOR UPDATE reasoning as the WhatsApp webhook: Telegram can
        # redeliver an update, and two near-simultaneous taps from the same
        # customer must not race each other's read-modify-write of
        # current_step/temp_data.
        result = await db.execute(
            select(TelegramSession)
            .where(
                TelegramSession.tenant_id == tenant.id,
                TelegramSession.customer_chat_id == chat_id,
            )
            .with_for_update()
        )
        session = result.scalar_one_or_none()
        if session is None:
            session = TelegramSession(tenant_id=tenant.id, customer_chat_id=chat_id)
            db.add(session)
            await db.flush()

        replies = await handle_incoming_message(db, tenant, session, text or "", callback_data)
        await db.commit()
        break

    if callback_query_id:
        try:
            await answer_callback_query(bot_token=bot_token, callback_query_id=callback_query_id)
        except TelegramSendError:
            pass  # cosmetic only — never block the actual reply on this

    for reply in replies:
        try:
            if isinstance(reply, dict) and reply.get("type") == "interactive_list":
                await send_inline_keyboard(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    body_text=reply["body_text"],
                    button_text=reply["button_text"],
                    sections=reply["sections"],
                )
            else:
                await send_text_message(bot_token=bot_token, chat_id=chat_id, body=reply)
        except TelegramSendError:
            logger.exception("Failed to send Telegram reply to chat_id=%s", chat_id)


# ---------------------------------------------------------------------------
# Admin endpoints (normal JWT auth, tenant-scoped) — same shape as the
# WhatsApp connect/disconnect/status/send endpoints
# ---------------------------------------------------------------------------

@router.post("/connect", response_model=TelegramStatusOut)
async def connect_telegram(
    body: TelegramConnectIn,
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    try:
        bot_info = await get_me(body.bot_token)
    except TelegramSendError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid bot token") from exc

    result = await db.execute(
        select(TelegramConfig).where(TelegramConfig.tenant_id == user.tenant_id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = TelegramConfig(tenant_id=user.tenant_id)
        db.add(config)
    config.bot_token = body.bot_token
    config.bot_username = bot_info.get("username")
    await db.flush()

    if settings.public_base_url:
        webhook_url = f"{settings.public_base_url.rstrip('/')}/api/v1/telegram/webhook/{body.bot_token}"
        try:
            await set_webhook(bot_token=body.bot_token, webhook_url=webhook_url)
        except TelegramSendError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, detail=f"Token saved, but registering the webhook with Telegram failed: {exc}"
            ) from exc

    await db.commit()
    return TelegramStatusOut(connected=True, bot_username=config.bot_username)


@router.post("/disconnect", response_model=TelegramStatusOut)
async def disconnect_telegram(
    user: TenantUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(TelegramConfig).where(TelegramConfig.tenant_id == user.tenant_id)
    )
    config = result.scalar_one_or_none()
    if config is not None:
        try:
            await delete_webhook(bot_token=config.bot_token)
        except TelegramSendError:
            pass  # best-effort; still disconnect locally either way
        await db.delete(config)
        await db.commit()
    return TelegramStatusOut(connected=False)


@router.get("/status", response_model=TelegramStatusOut)
async def telegram_status(
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(TelegramConfig).where(TelegramConfig.tenant_id == user.tenant_id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        return TelegramStatusOut(connected=False)
    return TelegramStatusOut(connected=True, bot_username=config.bot_username)


@router.post("/send")
async def send_telegram_message(
    body: TelegramSendIn,
    user: TenantUser = Depends(require_role("admin", "staff")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Manual/test send — mirrors WhatsApp's /send: useful to confirm the
    bot token works before relying on the automated flow."""
    result = await db.execute(
        select(TelegramConfig).where(TelegramConfig.tenant_id == user.tenant_id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="No Telegram bot connected for this tenant")
    try:
        result = await send_text_message(bot_token=config.bot_token, chat_id=body.chat_id, body=body.body)
    except TelegramSendError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return result
