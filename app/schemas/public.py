import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.scheduling import ServiceOut


class WorkingHoursDayOut(BaseModel):
    day: str
    is_open: bool
    windows: list[str]


class PublicTenantOut(BaseModel):
    name: str
    slug: str
    timezone: str
    branding: dict
    services: list[ServiceOut]
    cancellation_policy: str | None
    offers: str | None
    announcements: str | None
    about: str | None
    working_hours: list[WorkingHoursDayOut]


class AvailableSlotOut(BaseModel):
    scheduled_at: datetime
    duration_minutes: int


class PublicBookingCreate(BaseModel):
    service_id: uuid.UUID | None = None
    customer_name: str = Field(min_length=1, max_length=150)
    customer_phone: str = Field(pattern=r"^\+?[0-9]{7,15}$")
    customer_email: str | None = None
    scheduled_at: datetime
    notes: str | None = None

    @field_validator("customer_phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        """Same canonical form as AppointmentCreate (see its docstring) —
        keeping both write paths consistent is what lets lookup/cancel find
        a booking regardless of whether it was made here or entered by staff
        in the admin dashboard."""
        return v.strip().lstrip("+")


class PublicBookingOut(BaseModel):
    booking_ref: str
    status: str
    scheduled_at: datetime
    duration_minutes: int
    customer_name: str

    model_config = {"from_attributes": True}
