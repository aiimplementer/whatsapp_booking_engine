"""002 tenants.subscription_enabled

Adds the platform-admin subscription kill switch column, separate from the
existing `status` lifecycle enum (trial/active/inactive/suspended) — see the
column comment in app/models/tenant.py for why they're kept apart.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "subscription_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "subscription_enabled")
