import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import UUIDPKMixin


class TelegramSession(UUIDPKMixin, Base):
    """Conversation state for the Telegram channel.

    Deliberately a separate table from `whatsapp_sessions` rather than a
    shared/polymorphic one — this keeps the Telegram integration fully
    additive: nothing here is read or written by the WhatsApp webhook, and
    the WhatsApp table's shape/constraints stay untouched.

    Shape mirrors WhatsAppSession on purpose (current_step/temp_data) so the
    exact same `app.services.whatsapp_bot.handle_incoming_message` state
    machine can drive this channel too — that function only touches
    `.current_step`/`.temp_data` on whatever session object it's given, it
    never imports or checks WhatsAppSession specifically.
    """

    __tablename__ = "telegram_sessions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    # Telegram chat ids are 64-bit integers, but we store as text (as the
    # WhatsApp side does for phone numbers) since we only ever use it as an
    # opaque identifier to send replies back to, never do arithmetic on it.
    customer_chat_id: Mapped[str] = mapped_column(String(32))
    current_step: Mapped[str] = mapped_column(String(50), default="MAIN_MENU")
    temp_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), server_onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "customer_chat_id", name="uq_telegram_sessions_tenant_chat"),
    )

    @property
    def customer_phone(self) -> str:
        """Alias so this object duck-types the same as WhatsAppSession for
        `app.services.whatsapp_bot`'s purposes.

        That module reads `session.customer_phone` in three places — storing
        it on a new Appointment, and filtering existing Appointments by it
        for the "look up my booking" / "cancel my booking" flows — and does
        so consistently (always the same attribute, both writing and
        reading), so aliasing it to the Telegram chat id here is enough:
        an appointment booked over Telegram gets `customer_phone` set to
        the customer's chat id, and later lookups from that same chat id
        filter on the same value, exactly mirroring how a WhatsApp
        customer's phone number scopes their own bookings.

        Not a mapped column — this is a read-only Python property, so it
        adds no schema and touches nothing on the whatsapp_sessions/
        Appointment side.
        """
        return self.customer_chat_id
