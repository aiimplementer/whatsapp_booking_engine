from pydantic import BaseModel


class WhatsAppConnectIn(BaseModel):
    # Meta's phone_number_id for this tenant's WhatsApp sender number (from
    # the Meta App dashboard > WhatsApp > API Setup), not the human phone
    # number itself — see app/services/whatsapp_client.py for why.
    phone_number_id: str


class WhatsAppSendIn(BaseModel):
    to: str  # customer's number, international format, no leading '+'
    body: str


class WhatsAppStatusOut(BaseModel):
    connected: bool
    phone_number_id: str | None = None
