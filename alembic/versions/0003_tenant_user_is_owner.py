"""003 tenant_users.is_owner

Adds an is_owner flag identifying the user who originally created the
tenant (the admin created during signup). That user's role can never be
changed or removed by anyone — including other admins — whereas every
other tenant_user remains freely editable subject to the existing
"can't remove the last admin" rule.

A dedicated boolean is used rather than inferring "the owner" from
created_at ordering: ties are possible in theory, the intent is clearer
in the schema/API, and it survives future changes to how users get
created (e.g. a bulk import) without silently reassigning ownership.

Backfill: for tenants that already exist, the earliest-created admin
user (ties broken by id) is marked as owner, since that's the closest
available approximation of "who created the tenant" for pre-existing
data — signup itself only ever creates one admin per tenant, so in
practice this picks out exactly that user.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_users",
        sa.Column(
            "is_owner",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE tenant_users
        SET is_owner = true
        WHERE id IN (
            SELECT DISTINCT ON (tenant_id) id
            FROM tenant_users
            WHERE role = 'admin'
            ORDER BY tenant_id, created_at ASC, id ASC
        )
        """
    )


def downgrade() -> None:
    op.drop_column("tenant_users", "is_owner")
