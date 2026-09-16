import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import UUIDPKMixin, TimestampMixin


class TelegramConfig(UUIDPKMixin, TimestampMixin, Base):
    """One row per tenant that has connected a Telegram bot.

    Kept as its own table rather than a `telegram_bot_token` column on
    `Tenant` so this feature adds a table instead of touching
    app/models/tenant.py (and by extension anything else that already reads
    that model/table). A tenant with no row here simply has Telegram
    disconnected — same semantics as `tenants.whatsapp_number IS NULL` on
    the WhatsApp side, just modeled without editing that table.
    """

    __tablename__ = "telegram_configs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), unique=True
    )
    # The Bot API token from @BotFather, e.g. "123456:ABC-DEF...". This IS the
    # bearer credential for sending as this bot, so treat it like the
    # whatsapp_access_token setting — never echo it back in API responses.
    bot_token: Mapped[str] = mapped_column(String(255), unique=True)
    # Cached from getMe() at connect time, purely for display in the admin
    # UI ("Connected as @my_shop_bot") — never used for auth.
    bot_username: Mapped[str | None] = mapped_column(String(255))
