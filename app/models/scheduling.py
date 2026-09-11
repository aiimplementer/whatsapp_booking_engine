import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, DateTime, Date, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import UUIDPKMixin, TimestampMixin


class Service(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "services"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(150))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SchedulingConfig(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "scheduling_configs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    # NULL = tenant-wide default config; set = override for one specific service
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE")
    )
    working_days: Mapped[int] = mapped_column(SmallInteger, default=62)  # bitmask, Mon-Fri
    time_slots: Mapped[list] = mapped_column(JSONB, default=list)
    appointment_duration_minutes: Mapped[int] = mapped_column(Integer, default=20)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=0)
    advance_booking_days: Mapped[int] = mapped_column(Integer, default=30)


class Holiday(UUIDPKMixin, Base):
    __tablename__ = "holidays"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BlockedTime(UUIDPKMixin, Base):
    __tablename__ = "blocked_times"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecurringBlockedTime(UUIDPKMixin, TimestampMixin, Base):
    """
    Represents a recurring daily blocked time slot (e.g., lunch break 12:00-13:00 every day).
    """
    __tablename__ = "recurring_blocked_times"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    start_time: Mapped[str] = mapped_column(String(5))  # HH:MM
    end_time: Mapped[str] = mapped_column(String(5))    # HH:MM
    days_of_week: Mapped[int] = mapped_column(SmallInteger, default=127)  # bitmask
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)