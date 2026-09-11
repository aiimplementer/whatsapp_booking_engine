# WhatsApp Booking Engine — API scaffold

## RLS role setup (do this before anything else touches the DB as the app)

Postgres lets a table's *owner* bypass Row Level Security entirely, no matter
what `SET LOCAL app.current_tenant_id` says. Neon's default `neondb_owner`
role owns every table we created — so out of the box, RLS is silently a
no-op if the app connects with those credentials.

Run this once, in the Neon SQL Editor, on whichever branch you're using:

```sql
CREATE ROLE app_user WITH LOGIN PASSWORD 'generate-a-real-password-here';
GRANT CONNECT ON DATABASE neondb TO app_user;
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_user;
ALTER TABLE tenant_users        FORCE ROW LEVEL SECURITY;
ALTER TABLE services            FORCE ROW LEVEL SECURITY;
ALTER TABLE scheduling_configs  FORCE ROW LEVEL SECURITY;
ALTER TABLE holidays            FORCE ROW LEVEL SECURITY;
ALTER TABLE blocked_times       FORCE ROW LEVEL SECURITY;
ALTER TABLE appointments        FORCE ROW LEVEL SECURITY;
ALTER TABLE available_slots     FORCE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_sessions   FORCE ROW LEVEL SECURITY;
```

Then use `app_user`'s credentials in `DATABASE_URL` / `DATABASE_URL_SYNC`,
not `neondb_owner`'s.

## Auth

Four endpoints, all under `/api/v1/auth`:

| Endpoint | Purpose |
|---|---|
| `POST /signup` | Creates a new tenant + its first admin user in one call, returns tokens immediately. Body: `business_name`, `tenant_slug`, `business_email`, `business_phone`, `admin_email`, `admin_password`. |
| `POST /login` | Body: `tenant_slug`, `email`, `password`. Returns `access_token` + `refresh_token`. |
| `POST /refresh` | Body: `refresh_token`. Returns a new `access_token`. |
| `GET /me` | Requires `Authorization: Bearer <access_token>`. Returns the current user. |

Design choices worth knowing about before building on top of this:

- **Refresh tokens are stateless** — there's no DB table backing them, no
  logout-everywhere / revoke-on-compromise capability. A leaked refresh token
  stays valid until it naturally expires (`refresh_token_expire_days`, default
  14). This was a deliberate "ship faster" trade-off — if you need revocation
  later, that means adding a `refresh_tokens` table and checking it on every
  `/refresh` call, which is a bigger change than it sounds (touches login,
  refresh, and adds a logout endpoint that currently doesn't exist).
- **`tenant_slug` is required at login**, not just email/password — because
  RLS means we can't look up "which tenant does this email belong to" without
  already knowing the tenant. The frontend needs a business's slug (e.g. from
  their booking link) before it can show a login form.
- **Every protected endpoint should use `app.deps.get_tenant_db`** (derives
  tenant context from the verified JWT) for its DB session — never build a
  new dependency that takes `tenant_id` from a header, query param, or request
  body. That would let any authenticated user read another tenant's data just
  by changing a value in the request.
- Use `Depends(require_role("admin"))` (or `"admin", "staff"`) instead of
  `Depends(get_current_user)` on endpoints that should be role-restricted —
  e.g. only admins should be able to change scheduling config or invite staff.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in your real Neon credentials
```

Read `.env.example` carefully — Neon's console gives you a `postgresql://...sslmode=require`
string, but this project needs TWO variants of it (see the comments in that file
for the exact differences: `+asyncpg` driver marker and `ssl=` vs `sslmode=`).

## If you're pointing this at the `init` branch from the manual walkthrough

That branch already has the full schema applied by hand via the SQL Editor.
Tell Alembic "this is already done" instead of re-running the SQL:

```bash
alembic stamp head
```

## If you're pointing this at a brand new, empty Neon branch/project

```bash
alembic upgrade head
```

This runs `001_init_schema.sql` (embedded in `alembic/versions/0001_initial_schema.py`)
end to end, including the `appointment_tstzrange()` immutable-wrapper fix.

## Run the API

```bash
uvicorn app.main:app --reload
```

Then check `GET /health` — it does a real round-trip query to Neon, so if it
returns `{"status": "ok"}` your connection string is correct.

## Project layout

```
app/
  config.py       — env var loading (pydantic-settings)
  database.py     — async engine, session factory, RLS tenant-context plumbing
  deps.py         — FastAPI dependency that resolves tenant_id and yields a
                     tenant-scoped session (RLS-enforced)
  models/         — one SQLAlchemy model file per logical table group,
                     1:1 with 001_init_schema.sql
  main.py         — FastAPI app + health check; routers get mounted here
                     as each API group (tenants, appointments, scheduling,
                     public booking) gets built next
alembic/
  env.py          — wired to app.config.settings and app.models metadata
  versions/0001_initial_schema.py — baseline revision (see stamp vs upgrade above)
```

## Next steps (not yet built)

- Routers for the endpoint groups listed in the architecture doc
  (`/api/v1/tenants`, `/api/v1/appointments`, `/api/v1/scheduling`,
  `/api/v1/public/:tenant_slug/*`). All of these should depend on
  `app.deps.get_tenant_db` and, where appropriate, `require_role(...)`.
- The slot-generation service that populates `available_slots` from
  `scheduling_configs` + `holidays` + `blocked_times`.
- WhatsApp/n8n webhook integration — note this needs its OWN auth mechanism
  (webhook signature verification), not the JWT flow built here, since
  Meta/Twilio isn't a logged-in tenant user.
