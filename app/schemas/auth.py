import uuid

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    # Creates a brand-new tenant + its first admin user in one call.
    # Matches "Phase 1: Tenant Registration" in the architecture doc.
    business_name: str = Field(min_length=1, max_length=255)
    tenant_slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    business_email: EmailStr
    business_phone: str
    admin_email: EmailStr
    admin_password: str = Field(min_length=8)
    timezone: str = Field(default="Asia/Kolkata")  # Add this


class LoginRequest(BaseModel):
    tenant_slug: str
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    role: str

    model_config = {"from_attributes": True}
