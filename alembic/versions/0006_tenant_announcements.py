"""006 tenants.announcements

Adds a free-text "announcements" column on tenants, editable from the admin
dashboard's Business settings page (same pattern as cancellation_policy and
offers) and surfaced to customers via a new "Announcements" option on the
WhatsApp main menu.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("announcements", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "announcements")
