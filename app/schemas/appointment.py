import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

VALID_STATUSES = {
    "PENDING", "CONFIRMED", "CHECKED_IN", "COMPLETED",
    "CANCELLED", "RESCHEDULED", "NO_SHOW", "EXPIRED",
}

# Allowed forward transitions. Anything not listed here (e.g. COMPLETED ->
# PENDING) is rejected by the router rather than silently allowed — keeps the
# status column meaningful for reporting instead of a free-for-all enum.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"CONFIRMED", "CANCELLED", "EXPIRED"},
    "CONFIRMED": {"CHECKED_IN", "CANCELLED", "RESCHEDULED", "NO_SHOW"},
    "CHECKED_IN": {"COMPLETED", "CANCELLED"},
    "RESCHEDULED": {"CONFIRMED", "CANCELLED"},
    # Terminal states: no further transitions.
    "COMPLETED": set(),
    "CANCELLED": set(),
    "NO_SHOW": set(),
    "EXPIRED": set(),
}


class AppointmentCreate(BaseModel):
    """Staff-side creation (e.g. phone booking taken manually)."""

    service_id: uuid.UUID | None = None
    customer_name: str = Field(min_length=1, max_length=150)
    customer_phone: str = Field(pattern=r"^\+?[0-9]{7,15}$")
    customer_email: str | None = None
    scheduled_at: datetime
    duration_minutes: int = Field(gt=0)
    notes: str | None = None
    status: str = Field(default="CONFIRMED")

    @field_validator("customer_phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        """Store a canonical form (no leading '+') so a number typed here by
        staff lines up with the same number typed by a customer later on the
        public lookup form — otherwise '+919876543210' (entered here) and
        '919876543210' (typed at lookup) are the same phone number but fail
        the exact-match check that keeps one customer's booking private from
        another's."""
        return v.strip().lstrip("+")


class AppointmentUpdate(BaseModel):
    """PATCH semantics. Rescheduling (new scheduled_at/duration) and status
    changes can be combined in one call — e.g. move the time AND mark
    RESCHEDULED in a single request."""

    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    status: str | None = None
    notes: str | None = None


class AppointmentOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    service_id: uuid.UUID | None
    customer_name: str
    customer_phone: str
    customer_email: str | None
    scheduled_at: datetime
    duration_minutes: int
    status: str
    notes: str | None
    booking_ref: str
    created_at: datetime

    model_config = {"from_attributes": True}
