"""
Thin wrapper around the Telegram Bot API.

Multi-tenant routing: each tenant connects their own Telegram bot (created
via @BotFather) and we store its token in `telegram_configs.bot_token`. The
bot_token doubles as the tenant identifier for incoming webhooks: Telegram
POSTs updates to a URL of our choosing, so we register
`/api/v1/telegram/webhook/{bot_token}` per tenant at connect time and match
on that path segment the same way the WhatsApp side matches on
`metadata.phone_number_id`.

Mirrors app/services/whatsapp_client.py's shape (send_text_message,
WhatsAppSendError, verify_signature) so the two channels stay easy to read
side by side, but this module is entirely standalone — it does not import
from or alter whatsapp_client.py.
"""

import hmac

import httpx

from app.config import settings

API_BASE = "https://api.telegram.org"

# Telegram inline keyboards render better as one button per row for longer
# labels (WhatsApp's list rows can wrap; Telegram inline buttons truncate
# more aggressively), so we keep one button per line rather than trying to
# pack rows side by side.
ROW_TITLE_MAX = 64


class TelegramSendError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Telegram send failed ({status_code}): {body}")


def verify_secret_token(header_value: str | None) -> bool:
    """Validate Telegram's optional X-Telegram-Bot-Api-Secret-Token header.

    Telegram only sends this header if we supplied a `secret_token` when
    calling setWebhook. If the operator hasn't configured one
    (`telegram_webhook_secret` unset — e.g. local dev), we skip the check:
    the per-tenant bot_token embedded in the webhook path already acts as
    the shared secret, same role phone_number_id + the Meta app secret play
    together on the WhatsApp side.
    """
    if not settings.telegram_webhook_secret:
        return True
    if not header_value:
        return False
    return hmac.compare_digest(settings.telegram_webhook_secret, header_value)


async def get_me(bot_token: str) -> dict:
    """Calls getMe — used at /connect time to confirm the token is valid
    before we save it, and to cache the bot's @username for display."""
    url = f"{API_BASE}/bot{bot_token}/getMe"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
    data = resp.json()
    if resp.status_code >= 400 or not data.get("ok"):
        raise TelegramSendError(resp.status_code, resp.text)
    return data["result"]


async def send_text_message(*, bot_token: str, chat_id: str, body: str) -> dict:
    url = f"{API_BASE}/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": body}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
    data = resp.json()
    if resp.status_code >= 400 or not data.get("ok"):
        raise TelegramSendError(resp.status_code, resp.text)
    return data


async def send_inline_keyboard(
    *,
    bot_token: str,
    chat_id: str,
    body_text: str,
    button_text: str,
    sections: list[dict],
) -> dict:
    """Telegram has no native "list message" like WhatsApp's interactive
    list, so this renders the same {sections: [{title, rows}]} shape (as
    produced by app.services.whatsapp_bot's _render_* helpers) as an inline
    keyboard: one tappable button per row, with the row's "id" carried
    through as callback_data. That id is exactly what
    whatsapp_bot._choice_index() already knows how to parse, so tapping a
    Telegram button resolves the same way a tapped WhatsApp list row does.

    Section titles have no direct inline-keyboard equivalent, so they're
    folded into the body text above the buttons instead of being dropped.
    button_text (WhatsApp's list-opening button label) isn't needed here
    since the buttons are already visible — kept as a parameter only so
    callers can pass the same reply dict without branching.
    """
    lines = [body_text]
    keyboard: list[list[dict]] = []
    for section in sections:
        title = section.get("title")
        if title:
            lines.append(f"\n*{title}*")
        for row in section.get("rows", []):
            label = row["title"]
            if row.get("description"):
                label = f"{label} — {row['description']}"
            keyboard.append([{"text": label[:ROW_TITLE_MAX], "callback_data": row["id"]}])

    url = f"{API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "\n".join(lines),
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": keyboard},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
    data = resp.json()
    if resp.status_code >= 400 or not data.get("ok"):
        raise TelegramSendError(resp.status_code, resp.text)
    return data


async def answer_callback_query(*, bot_token: str, callback_query_id: str) -> None:
    """Clears the tap-loading spinner on the button the customer pressed.
    Best-effort: callers should swallow failures here rather than treat
    them as a reason to skip sending the actual reply."""
    url = f"{API_BASE}/bot{bot_token}/answerCallbackQuery"
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(url, json={"callback_query_id": callback_query_id})


async def set_webhook(*, bot_token: str, webhook_url: str) -> dict:
    """Registers our webhook URL with Telegram — called once at /connect
    time so the admin doesn't have to hit the Bot API by hand."""
    url = f"{API_BASE}/bot{bot_token}/setWebhook"
    payload = {"url": webhook_url}
    if settings.telegram_webhook_secret:
        payload["secret_token"] = settings.telegram_webhook_secret
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
    data = resp.json()
    if resp.status_code >= 400 or not data.get("ok"):
        raise TelegramSendError(resp.status_code, resp.text)
    return data


async def delete_webhook(*, bot_token: str) -> None:
    url = f"{API_BASE}/bot{bot_token}/deleteWebhook"
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(url)
