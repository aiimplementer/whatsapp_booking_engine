"""004 tenants.cancellation_policy

Adds a free-text cancellation policy column on tenants, editable from the
admin dashboard's Business settings page and surfaced to customers via the
new "Cancellation Policy" option on the WhatsApp main menu.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("cancellation_policy", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "cancellation_policy")
