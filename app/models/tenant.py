import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import UUIDPKMixin, TimestampMixin

# create_type=False: these ENUM TYPEs already exist in the DB from
# 001_init_schema.sql. Alembic/SQLAlchemy must NOT try to CREATE TYPE again.
tenant_status_enum = ENUM(
    "trial", "active", "inactive", "suspended",
    name="tenant_status", create_type=False,
)
subscription_tier_enum = ENUM(
    "free", "starter", "pro", "enterprise",
    name="subscription_tier", create_type=False,
)
tenant_user_role_enum = ENUM(
    "admin", "staff", "viewer",
    name="tenant_user_role", create_type=False,
)


class Tenant(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    phone: Mapped[str] = mapped_column(String(20))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(tenant_status_enum, default="trial")
    subscription_tier: Mapped[str] = mapped_column(subscription_tier_enum, default="free")
    whatsapp_number: Mapped[str | None] = mapped_column(String(20), unique=True)
    web_booking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Platform-operator kill switch, deliberately separate from `status`:
    # `status` (trial/active/inactive/suspended) is the tenant/product lifecycle
    # state, whereas this is purely "is their subscription/billing current" as
    # toggled from the platform admin console. Keeping them apart means billing
    # ops never has to reason about (or accidentally clobber) product-status
    # transitions, and vice versa.
    subscription_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Free-text policy shown to customers (e.g. via the WhatsApp bot's
    # "Cancellation Policy" menu option). Optional — tenants that haven't
    # filled it in yet just get a generic fallback message at read time.
    cancellation_policy: Mapped[str | None] = mapped_column(Text)
    # Free-text current offers/promotions, shown to customers via the
    # WhatsApp bot's "Offers" menu option. Same pattern as
    # cancellation_policy: optional, plain text, empty = nothing to show.
    offers: Mapped[str | None] = mapped_column(Text)
    branding: Mapped[dict] = mapped_column(JSONB, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    users: Mapped[list["TenantUser"]] = relationship(back_populates="tenant")


class TenantUser(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "tenant_users"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(tenant_user_role_enum, default="staff")
    password_hash: Mapped[str] = mapped_column(String(255))
    # The user created alongside the tenant at signup — see migration 0003.
    # Their role/removal is locked regardless of who is asking, including
    # other admins; every other tenant_user stays freely editable.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
