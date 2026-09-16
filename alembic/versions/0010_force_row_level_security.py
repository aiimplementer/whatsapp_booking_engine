"""010 force row level security

Closes the actual gap behind the "login only checks user/pass" bug: RLS
policies were enabled on every tenant-scoped table (0001, 0007) but never
FORCEd. In Postgres, the table OWNER bypasses RLS policies by default,
regardless of ENABLE ROW LEVEL SECURITY — only FORCE ROW LEVEL SECURITY
makes a policy apply to the owner too. Since the app connects with the
same role that ran the CREATE TABLE statements (it owns every table here),
every tenant_isolation policy has been silently inert for that role: any
query missing an explicit tenant_id filter (e.g. app/routers/auth.py's
login lookup before this fix) fell through to matching across ALL tenants
instead of being scoped by the app.current_tenant_id session variable.

This migration does not change app behavior for queries that already
filter by tenant_id explicitly (the normal, correct pattern this app
already follows almost everywhere) — it only removes the silent bypass
for the connecting role, so RLS is actually an enforced second layer
rather than a policy that exists on paper only.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-16
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_TENANT_SCOPED_TABLES = [
    "tenant_users",
    "services",
    "scheduling_configs",
    "holidays",
    "blocked_times",
    "appointments",
    "available_slots",
    "whatsapp_sessions",
    "telegram_configs",
    "telegram_sessions",
]


def upgrade() -> None:
    for table in _TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in _TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
