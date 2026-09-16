"""008 appointments.telegram_contact_phone

Adds a nullable `telegram_contact_phone` column to `appointments`: the
verified phone number a Telegram customer shares via the bot's native
"request contact" button, captured by the new AWAIT_CONTACT step in
app/services/whatsapp_bot.py.

Purely additive — does not touch `customer_phone` (which keeps its existing
meaning/behavior for both channels, including the WhatsApp flow and the
Telegram "look up/cancel my booking" lookup, which still matches on it),
`whatsapp_sessions`, the public booking API, or the admin dashboard.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("telegram_contact_phone", sa.String(length=20), nullable=True),
    )
    op.execute(
        r"""
        ALTER TABLE appointments
            ADD CONSTRAINT chk_appt_telegram_contact_phone_format
            CHECK (telegram_contact_phone IS NULL OR telegram_contact_phone ~ '^\+?[0-9]{7,15}$')
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE appointments DROP CONSTRAINT IF EXISTS chk_appt_telegram_contact_phone_format"
    )
    op.drop_column("appointments", "telegram_contact_phone")
