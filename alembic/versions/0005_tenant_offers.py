"""005 tenants.offers

Adds a free-text "current offers / promotions" column on tenants, editable
from the admin dashboard's Business settings page (same pattern as
cancellation_policy) and surfaced to customers via a new "Offers" option on
the WhatsApp main menu.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("offers", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "offers")
