import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


# ----------------------------------------------------------------------------
# Services
# ----------------------------------------------------------------------------

class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    duration_minutes: int = Field(gt=0)
    description: str | None = None


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    duration_minutes: int | None = Field(default=None, gt=0)
    description: str | None = None
    active: bool | None = None


class ServiceOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    duration_minutes: int
    description: str | None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ----------------------------------------------------------------------------
# Scheduling configs
# ----------------------------------------------------------------------------

class TimeSlotWindow(BaseModel):
    """One working window on one weekday. day: 0=Sun .. 6=Sat, matching the
    working_days bitmask convention in the schema (bit0=Sun..bit6=Sat)."""

    day: int = Field(ge=0, le=6)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM, 24h")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM, 24h")

    @field_validator("end")
    @classmethod
    def end_after_start(cls, end: str, info):
        start = info.data.get("start")
        if start is not None and end <= start:
            raise ValueError("end must be after start")
        return end


class SchedulingConfigUpsert(BaseModel):
    """Used for both create and update — the (tenant_id, service_id) unique
    constraint means there's naturally at most one config per service (or one
    tenant-wide default), so this endpoint upserts rather than exposing
    separate create/update semantics the caller would have to pick between."""

    service_id: uuid.UUID | None = Field(
        default=None, description="Omit/null for the tenant-wide default config"
    )
    working_days: int = Field(default=62, ge=0, le=127)
    time_slots: list[TimeSlotWindow] = Field(default_factory=list)
    appointment_duration_minutes: int = Field(default=20, gt=0)
    buffer_minutes: int = Field(default=0, ge=0)
    advance_booking_days: int = Field(default=30, gt=0)


class SchedulingConfigOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    service_id: uuid.UUID | None
    working_days: int
    time_slots: list[dict]
    appointment_duration_minutes: int
    buffer_minutes: int
    advance_booking_days: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ----------------------------------------------------------------------------
# Holidays
# ----------------------------------------------------------------------------

class HolidayCreate(BaseModel):
    date: date
    reason: str | None = Field(default=None, max_length=255)


class HolidayOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    date: date
    reason: str | None

    model_config = {"from_attributes": True}


# ----------------------------------------------------------------------------
# Blocked times
# ----------------------------------------------------------------------------

class BlockedTimeCreate(BaseModel):
    start_datetime: datetime
    end_datetime: datetime
    reason: str | None = Field(default=None, max_length=255)

    @field_validator("end_datetime")
    @classmethod
    def end_after_start(cls, end_datetime: datetime, info):
        start_datetime = info.data.get("start_datetime")
        if start_datetime is not None and end_datetime <= start_datetime:
            raise ValueError("end_datetime must be after start_datetime")
        return end_datetime


class BlockedTimeOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    start_datetime: datetime
    end_datetime: datetime
    reason: str | None

    model_config = {"from_attributes": True}


class RecurringBlockedTimeCreate(BaseModel):
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days_of_week: int = Field(default=127, ge=0, le=127)
    start_date: date | None = None
    end_date: date | None = None
    reason: str | None = Field(default=None, max_length=255)

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, end_time: str, info):
        start_time = info.data.get("start_time")
        if start_time and end_time <= start_time:
            raise ValueError("end_time must be after start_time")
        return end_time

    @field_validator("end_date")
    @classmethod
    def end_date_after_start_date(cls, end_date: date | None, info):
        if end_date is None:
            return end_date
        start_date = info.data.get("start_date")
        if start_date and end_date < start_date:
            raise ValueError("end_date must be >= start_date")
        return end_date


class RecurringBlockedTimeUpdate(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    days_of_week: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    reason: str | None = None
    active: bool | None = None


class RecurringBlockedTimeOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    start_time: str
    end_time: str
    days_of_week: int
    start_date: date | None
    end_date: date | None
    reason: str | None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}