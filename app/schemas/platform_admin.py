import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


class PlatformTenantOut(BaseModel):
    """One row of the platform admin console's tenant list."""

    id: uuid.UUID
    name: str
    slug: str
    email: EmailStr
    timezone: str
    status: str
    subscription_tier: str
    subscription_enabled: bool
    web_booking_enabled: bool
    admin_email: str | None = None  # primary (earliest-added) tenant_user with role=admin
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedTenants(BaseModel):
    items: list[PlatformTenantOut]
    total: int
    page: int
    page_size: int
    pages: int


class SubscriptionUpdate(BaseModel):
    enabled: bool


class PlatformAdminLoginRequest(BaseModel):
    username: str
    password: str


class PlatformAdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str
