import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    email: EmailStr
    phone: str
    timezone: str
    status: str
    subscription_tier: str
    whatsapp_number: str | None
    web_booking_enabled: bool
    cancellation_policy: str | None
    branding: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantUpdate(BaseModel):
    """All fields optional — PATCH semantics, only supplied fields are changed.

    Deliberately excludes slug/email/status/subscription_tier: slug is a public
    URL identity best not changed casually, email doubles as the account's
    unique identifier, and status/subscription_tier are billing/ops concerns
    that shouldn't be self-service from this endpoint.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = None
    timezone: str | None = None
    whatsapp_number: str | None = None
    web_booking_enabled: bool | None = None
    cancellation_policy: str | None = Field(default=None, max_length=4000)
    branding: dict | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"'{v}' is not a valid IANA timezone name (e.g. 'Asia/Kolkata', 'America/New_York')"
            ) from exc
        return v


class TenantUserOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    role: str
    is_owner: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = Field(default="staff", pattern="^(admin|staff|viewer)$")


class TenantUserRoleUpdate(BaseModel):
    role: str = Field(pattern="^(admin|staff|viewer)$")
