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


settings = Settings()
