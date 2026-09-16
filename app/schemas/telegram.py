from pydantic import BaseModel


class TelegramConnectIn(BaseModel):
    # Bot token from @BotFather, e.g. "123456789:AAH...". This is a secret —
    # never returned in any response, only accepted as input.
    bot_token: str


class TelegramSendIn(BaseModel):
    chat_id: str  # customer's Telegram chat id (as sent to us in updates)
    body: str


class TelegramStatusOut(BaseModel):
    connected: bool
    bot_username: str | None = None
