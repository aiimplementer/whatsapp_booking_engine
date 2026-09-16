"""007 telegram connector

Adds the Telegram channel as a new, fully additive connector: two new
tables (telegram_configs, telegram_sessions), each with the same
tenant_isolation RLS policy pattern as every other tenant-scoped table.

Nothing here alters `tenants`, `whatsapp_sessions`, or any existing table,
type, function, or policy — this migration only CREATEs new objects.

Each statement is executed individually (rather than one big multi-statement
string, as in migrations 0001-0006) because this project's Alembic env runs
through the asyncpg driver, and asyncpg's extended query protocol rejects
"cannot insert multiple commands into a prepared statement" for anything
that isn't exactly one SQL command per execute() call.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-16
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_UPGRADE_STATEMENTS = [
    # --- TELEGRAM BOT CONFIG (per-tenant bot credentials) ---
    r"""
    CREATE TABLE telegram_configs (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id       UUID NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
        bot_token       VARCHAR(255) NOT NULL UNIQUE,
        bot_username    VARCHAR(255),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    r"""
    CREATE TRIGGER trg_telegram_configs_updated_at BEFORE UPDATE ON telegram_configs
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """,
    # --- TELEGRAM SESSION STATE (bot conversation state machine, mirrors
    # whatsapp_sessions but is a wholly separate table) ---
    r"""
    CREATE TABLE telegram_sessions (
        id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        customer_chat_id    VARCHAR(32) NOT NULL,
        current_step        VARCHAR(50) NOT NULL DEFAULT 'MAIN_MENU',
        temp_data           JSONB NOT NULL DEFAULT '{}'::jsonb,
        last_activity       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

        CONSTRAINT uq_telegram_sessions_tenant_chat UNIQUE (tenant_id, customer_chat_id)
    )
    """,
    r"""
    CREATE INDEX idx_telegram_sessions_activity ON telegram_sessions(last_activity)
    """,
    r"""
    CREATE TRIGGER trg_telegram_sessions_updated_at BEFORE UPDATE ON telegram_sessions
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """,
    # --- ROW LEVEL SECURITY ---
    r"""ALTER TABLE telegram_configs  ENABLE ROW LEVEL SECURITY""",
    r"""ALTER TABLE telegram_sessions ENABLE ROW LEVEL SECURITY""",
    r"""
    CREATE POLICY tenant_isolation ON telegram_configs
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
    """,
    r"""
    CREATE POLICY tenant_isolation ON telegram_sessions
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
    """,
]

_DOWNGRADE_STATEMENTS = [
    r"""DROP TABLE IF EXISTS telegram_sessions CASCADE""",
    r"""DROP TABLE IF EXISTS telegram_configs CASCADE""",
]


def upgrade() -> None:
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE_STATEMENTS:
        op.execute(statement)
