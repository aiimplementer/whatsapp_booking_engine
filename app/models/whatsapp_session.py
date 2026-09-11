import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import UUIDPKMixin


class WhatsAppSession(UUIDPKMixin, Base):
    __tablename__ = "whatsapp_sessions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    customer_phone: Mapped[str] = mapped_column(String(20))
    current_step: Mapped[str] = mapped_column(String(50), default="MAIN_MENU")
    temp_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), server_onupdate=func.now())

    __table_args__ = (
        # One live conversation per customer per tenant — the bot's message
        # handler upserts on this key rather than inserting a new row per message.
        UniqueConstraint("tenant_id", "customer_phone", name="uq_whatsapp_sessions_tenant_phone"),
    )
