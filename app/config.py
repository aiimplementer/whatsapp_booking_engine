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



settings = Settings()
