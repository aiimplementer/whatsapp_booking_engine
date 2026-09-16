"""
Import every model here so that a single `import app.models` (which Alembic's
env.py does) registers all tables on Base.metadata. Miss one and Alembic's
autogenerate will think that table doesn't exist and try to drop it.
"""

from app.models.tenant import Tenant, TenantUser  # noqa: F401
from app.models.scheduling import Service, SchedulingConfig, Holiday, BlockedTime  # noqa: F401
from app.models.appointment import Appointment, AvailableSlot  # noqa: F401
from app.models.whatsapp_session import WhatsAppSession  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.telegram_config import TelegramConfig  # noqa: F401
from app.models.telegram_session import TelegramSession  # noqa: F401
