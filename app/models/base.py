import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPKMixin:
    """Matches `id UUID PRIMARY KEY DEFAULT uuid_generate_v4()` in the schema.

    server_default is deliberately a raw SQL call, not a Python-side uuid4() —
    the DB (via the uuid-ossp extension) is the source of truth for these ids,
    same as the migration set up. Keeps behavior identical whether a row is
    inserted through the ORM, a script, or the WhatsApp/n8n workflow directly.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
    )


class TimestampMixin:
    """Matches `created_at`/`updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.

    updated_at is also touched by the `touch_updated_at()` trigger in the DB on
    every UPDATE — server_onupdate here just keeps the ORM's in-memory object
    in sync after a flush; the DB trigger is still what actually guarantees it.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), server_onupdate=func.now()
    )
