"""012 tenants.about

Adds a free-text "about" column on tenants, editable from the admin
dashboard's Business settings page (same pattern as cancellation_policy,
offers and announcements) and surfaced to customers as the first tab on the
public booking page.

Nullable with no default, so existing tenants are unaffected: a tenant that
never fills it in renders the booking page exactly as it did before, with
"New booking" as the opening tab.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("about", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "about")
