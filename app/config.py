from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    database_url_sync: str
    environment: str = "development"
    timezone: str = "Asia/Kolkata"

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    # --- WhatsApp Cloud API (Meta) ---
    # Arbitrary string you invent yourself and paste into the Meta App
    # dashboard's webhook config — Meta echoes it back on the GET verification
    # request so we can confirm the callback URL belongs to us.
    whatsapp_verify_token: str = ""
    # From Meta App dashboard > App Settings > Basic > App Secret. Used to
    # validate the X-Hub-Signature-256 header on every incoming POST, so we
    # can trust a webhook body actually came from Meta and wasn't forged.
    whatsapp_app_secret: str = ""
    # A permanent token for a System User (NOT the 24h temporary token shown
    # in Meta's quickstart) with whatsapp_business_messaging permission.
    whatsapp_access_token: str = ""
    whatsapp_api_version: str = "v21.0"

    # --- Platform admin console (/platform-admin) ---
    # Single shared operator account, deliberately nominal (HTTP Basic, no
    # sessions/roles/DB table) — this console is for the SaaS operator, not
    # tenants, and isn't meant to grow beyond one or two trusted people.
    # Generate the hash with:
    #   python -c "from app.security import hash_password; print(hash_password('yourpassword'))"
    platform_admin_username: str = "platform-admin"
    platform_admin_password_hash: str = ""

    # --- Telegram Bot API connector (additive; independent of WhatsApp) ---
    # Optional shared secret Telegram echoes back in the
    # X-Telegram-Bot-Api-Secret-Token header on every webhook POST, if set
    # here and passed to setWebhook. See app/services/telegram_client.py for
    # why this is optional (the per-tenant bot_token in the webhook path is
    # the primary secret).
    telegram_webhook_secret: str = ""
    # Public base URL of this deployment (e.g. "https://mybooking.app"),
    # used only to auto-register a tenant's webhook URL with Telegram at
    # /connect time. If left blank, /connect still saves the bot token but
    # the admin (or the tenant) must call Telegram's setWebhook manually.
    public_base_url: str = ""

    # --- Gmail OAuth2 (appointment booked/cancelled emails) ---
    # A single Google Workspace/Gmail account sends on behalf of the whole
    # platform (not per-tenant) — the "From" a customer sees is this one
    # mailbox. OAuth2 client credentials + a long-lived refresh token, from
    # the OAuth consent screen you already set up in Google Cloud Console:
    #   1. Cloud Console > APIs & Services > Credentials > OAuth client ID
    #      (type: Web application) -> gives you client_id/client_secret.
    #   2. Run the OAuth consent flow once with scope
    #      https://www.googleapis.com/auth/gmail.send and
    #      access_type=offline, prompt=consent, to obtain a refresh_token
    #      (e.g. via Google's OAuth Playground: https://developers.google.com/oauthplayground,
    #      using your own client_id/client_secret under its gear icon).
    #   3. Paste all three below (as Render env vars in prod).
    # Leave gmail_refresh_token blank to disable email sending entirely —
    # every call site treats that as "not configured" and skips silently.
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    # The mailbox the refresh token belongs to — also used as the visible
    # "From" address (Gmail's API always sends as the authenticated user
    # regardless of any From header, so this is what customers will see).
    gmail_sender_email: str = ""
    gmail_sender_name: str = "ScheduleMate"


settings = Settings()
