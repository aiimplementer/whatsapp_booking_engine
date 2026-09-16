"""009 unify telegram customer_phone with the rest of the app

0008 added a separate `appointments.telegram_contact_phone` column to hold
a Telegram customer's verified number, leaving `appointments.customer_phone`
holding the chat id for Telegram bookings. That's now reverted: the
verified number captured via the bot's "share contact" step is written
straight into `customer_phone`, exactly like every other channel already
does, so `telegram_contact_phone` is no longer needed.

To keep the existing "look up/cancel my booking" flow working — it filters
`Appointment.customer_phone == <this chat's known phone>` — this also adds
a `verified_phone` column to `telegram_sessions`. Unlike `temp_data` (which
is cleared after every booking), this persists across the conversation, so
once a customer has shared their number it keeps scoping their bookings on
every later visit to the same chat. Appointments booked before this change
(where `customer_phone` still holds the chat id) remain reachable until the
customer shares contact again, since `TelegramSession.customer_phone` falls
back to the chat id whenever `verified_phone` is still NULL.

Purely additive/corrective for the Telegram connector — does not touch
`whatsapp_sessions`, the public booking API, or the admin dashboard, all of
which only ever read `appointments.customer_phone` and never touched
`telegram_contact_phone` in the first place.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- appointments: drop the now-unnecessary separate column ---
    op.execute(
        "ALTER TABLE appointments DROP CONSTRAINT IF EXISTS chk_appt_telegram_contact_phone_format"
    )
    op.drop_column("appointments", "telegram_contact_phone")

    # --- telegram_sessions: persist the verified number once shared ---
    op.add_column(
        "telegram_sessions",
        sa.Column("verified_phone", sa.String(length=20), nullable=True),
    )
    op.execute(
        r"""
        ALTER TABLE telegram_sessions
            ADD CONSTRAINT chk_telegram_sessions_verified_phone_format
            CHECK (verified_phone IS NULL OR verified_phone ~ '^\+?[0-9]{7,15}$')
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE telegram_sessions DROP CONSTRAINT IF EXISTS chk_telegram_sessions_verified_phone_format"
    )
    op.drop_column("telegram_sessions", "verified_phone")

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
