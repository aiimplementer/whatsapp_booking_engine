"""013 tenants.email_notifications_enabled

Adds a per-tenant on/off switch for the booked/cancelled confirmation
emails sent to the customer's email address (Appointment.customer_email)
via the platform's Gmail OAuth2 sender. Same pattern as
web_booking_enabled: a plain boolean column, defaulted so existing tenants
don't suddenly start emailing customers just because the platform
configured Gmail credentials.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "email_notifications_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Drop the server_default after backfilling existing rows — matches the
    # ORM model, which sets the default only at the Python/insert layer, not
    # a standing DB-level default beyond what's needed for this migration's
    # own backfill of existing rows.
    op.alter_column("tenants", "email_notifications_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("tenants", "email_notifications_enabled")
