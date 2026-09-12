-- ============================================================================
-- WhatsApp Appointment Booking Engine
-- Migration 001: Initial schema (multi-tenant, Neon/Postgres)
-- ============================================================================

-- Needed for EXCLUDE constraint that prevents overlapping appointments
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "btree_gist";

-- ============================================================================
-- ENUM TYPES
-- ============================================================================

CREATE TYPE tenant_status AS ENUM ('trial', 'active', 'inactive', 'suspended');
CREATE TYPE subscription_tier AS ENUM ('free', 'starter', 'pro', 'enterprise');
CREATE TYPE tenant_user_role AS ENUM ('admin', 'staff', 'viewer');
CREATE TYPE appointment_status AS ENUM (
    'PENDING', 'CONFIRMED', 'CHECKED_IN', 'COMPLETED',
    'CANCELLED', 'RESCHEDULED', 'NO_SHOW', 'EXPIRED'
);

-- ============================================================================
-- TENANTS
-- ============================================================================

CREATE TABLE tenants (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                VARCHAR(255) NOT NULL,
    slug                VARCHAR(100) NOT NULL,           -- used in /public/:tenant_slug/*
    email               VARCHAR(255) NOT NULL,
    phone               VARCHAR(20)  NOT NULL,
    timezone            VARCHAR(64)  NOT NULL DEFAULT 'Asia/Kolkata',
    status              tenant_status NOT NULL DEFAULT 'trial',
    subscription_tier   subscription_tier NOT NULL DEFAULT 'free',
    whatsapp_number     VARCHAR(20),                      -- nullable until Phase 2 of onboarding
    web_booking_enabled BOOLEAN NOT NULL DEFAULT true,
    branding            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,                      -- soft delete

    CONSTRAINT uq_tenants_email UNIQUE (email),
    CONSTRAINT uq_tenants_slug  UNIQUE (slug),
    CONSTRAINT uq_tenants_whatsapp_number UNIQUE (whatsapp_number),
    CONSTRAINT chk_tenants_slug_format CHECK (slug ~ '^[a-z0-9-]+$')
);

CREATE INDEX idx_tenants_status ON tenants(status) WHERE deleted_at IS NULL;

-- ----------------------------------------------------------------------------

CREATE TABLE tenant_users (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email       VARCHAR(255) NOT NULL,
    role        tenant_user_role NOT NULL DEFAULT 'staff',
    password_hash VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_tenant_users_tenant_email UNIQUE (tenant_id, email)
);

CREATE INDEX idx_tenant_users_tenant ON tenant_users(tenant_id);

-- ============================================================================
-- SERVICES (optional, multi-service businesses)
-- ============================================================================

CREATE TABLE services (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        VARCHAR(150) NOT NULL,
    duration_minutes INT NOT NULL,
    description TEXT,
    active      BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_services_duration_positive CHECK (duration_minutes > 0)
);

CREATE INDEX idx_services_tenant ON services(tenant_id) WHERE active = true;

-- ============================================================================
-- SCHEDULING CONFIG
-- ============================================================================

CREATE TABLE scheduling_configs (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    service_id  UUID REFERENCES services(id) ON DELETE CASCADE,  -- NULL = tenant-wide default

    working_days       SMALLINT NOT NULL DEFAULT 62,  -- bitmask, bit0=Sun..bit6=Sat; 62 = Mon-Fri
    time_slots          JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{day,start,end}, ...]
    appointment_duration_minutes INT NOT NULL DEFAULT 20,
    buffer_minutes      INT NOT NULL DEFAULT 0,
    advance_booking_days INT NOT NULL DEFAULT 30,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_scheduling_configs_tenant_service UNIQUE (tenant_id, service_id),
    CONSTRAINT chk_sched_duration_positive CHECK (appointment_duration_minutes > 0),
    CONSTRAINT chk_sched_buffer_nonneg CHECK (buffer_minutes >= 0),
    CONSTRAINT chk_sched_advance_days_positive CHECK (advance_booking_days > 0)
);

CREATE INDEX idx_scheduling_configs_tenant ON scheduling_configs(tenant_id);

-- ----------------------------------------------------------------------------

CREATE TABLE holidays (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    date        DATE NOT NULL,
    reason      VARCHAR(255),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_holidays_tenant_date UNIQUE (tenant_id, date)
);

CREATE INDEX idx_holidays_tenant_date ON holidays(tenant_id, date);

-- ----------------------------------------------------------------------------

CREATE TABLE blocked_times (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    start_datetime  TIMESTAMPTZ NOT NULL,
    end_datetime    TIMESTAMPTZ NOT NULL,
    reason          VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_blocked_times_range CHECK (end_datetime > start_datetime)
);

CREATE INDEX idx_blocked_times_tenant_datetime ON blocked_times(tenant_id, start_datetime);

-- ============================================================================
-- APPOINTMENTS  (the core table — double-booking prevented at DB level)
-- ============================================================================

-- tstzrange() itself is marked STABLE by Postgres core (conservatively), but a
-- STORED generated column requires an IMMUTABLE expression. This wrapper is safe
-- to mark IMMUTABLE because timestamptz is always stored internally as UTC —
-- the arithmetic here never actually depends on session timezone.
CREATE FUNCTION appointment_tstzrange(ts TIMESTAMPTZ, minutes INT)
RETURNS TSTZRANGE AS $$
    SELECT tstzrange(ts, ts + (minutes || ' minutes')::interval, '[)');
$$ LANGUAGE sql IMMUTABLE;

CREATE TABLE appointments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    service_id      UUID REFERENCES services(id) ON DELETE SET NULL,

    customer_name   VARCHAR(150) NOT NULL,
    customer_phone  VARCHAR(20)  NOT NULL,
    customer_email  VARCHAR(255),

    scheduled_at    TIMESTAMPTZ NOT NULL,
    duration_minutes INT NOT NULL,

    -- Generated column purely so the EXCLUDE constraint below can operate on a range
    appointment_range TSTZRANGE GENERATED ALWAYS AS (
        appointment_tstzrange(scheduled_at, duration_minutes)
    ) STORED,

    status          appointment_status NOT NULL DEFAULT 'PENDING',
    notes           TEXT,
    reminder_sent_at TIMESTAMPTZ,
    booking_ref     VARCHAR(20) NOT NULL,       -- e.g. APT-12345678, shown to customer
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_appt_duration_positive CHECK (duration_minutes > 0),
    CONSTRAINT chk_appt_phone_format CHECK (customer_phone ~ '^\+?[0-9]{7,15}$'),
    CONSTRAINT uq_appointments_booking_ref UNIQUE (booking_ref),

    -- No two ACTIVE appointments for the same tenant can overlap in time.
    -- Cancelled/completed/no-show/expired appointments are excluded from this check
    -- via the WHERE clause, so history doesn't block new bookings on that slot.
    CONSTRAINT excl_appointments_no_overlap EXCLUDE USING gist (
        tenant_id WITH =,
        appointment_range WITH &&
    ) WHERE (status IN ('PENDING', 'CONFIRMED', 'CHECKED_IN'))
);

CREATE INDEX idx_appointments_tenant_scheduled ON appointments(tenant_id, scheduled_at);
CREATE INDEX idx_appointments_customer_phone ON appointments(tenant_id, customer_phone);
CREATE INDEX idx_appointments_status ON appointments(tenant_id, status);

-- ============================================================================
-- AVAILABLE SLOTS (computed cache — safe to regenerate/upsert nightly)
-- ============================================================================

CREATE TABLE available_slots (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    service_id      UUID REFERENCES services(id) ON DELETE CASCADE,
    slot_datetime   TIMESTAMPTZ NOT NULL,
    duration_minutes INT NOT NULL,
    booked          BOOLEAN NOT NULL DEFAULT false,
    blocked         BOOLEAN NOT NULL DEFAULT false,
    reason          VARCHAR(255),
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_available_slots_identity
        UNIQUE (tenant_id, service_id, slot_datetime)
);

CREATE INDEX idx_available_slots_lookup
    ON available_slots(tenant_id, service_id, slot_datetime)
    WHERE booked = false AND blocked = false;

-- ============================================================================
-- WHATSAPP SESSION STATE (bot conversation state machine)
-- ============================================================================

CREATE TABLE whatsapp_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_phone  VARCHAR(20) NOT NULL,
    current_step    VARCHAR(50) NOT NULL DEFAULT 'MAIN_MENU',
    temp_data       JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_activity   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One live session per customer per tenant; app upserts on this key
    CONSTRAINT uq_whatsapp_sessions_tenant_phone UNIQUE (tenant_id, customer_phone)
);

CREATE INDEX idx_whatsapp_sessions_activity ON whatsapp_sessions(last_activity);

-- ============================================================================
-- AUDIT LOG
-- ============================================================================

CREATE TABLE audit_logs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID REFERENCES tenants(id) ON DELETE SET NULL,
    actor_type      VARCHAR(20) NOT NULL DEFAULT 'system',  -- 'user' | 'system' | 'customer'
    actor_id        UUID,
    action          VARCHAR(100) NOT NULL,
    resource_type   VARCHAR(50) NOT NULL,
    resource_id     UUID,
    changes         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_tenant_created ON audit_logs(tenant_id, created_at DESC);

-- ============================================================================
-- updated_at AUTO-TOUCH TRIGGER (applied to every table that has the column)
-- ============================================================================

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tenants_updated_at BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_tenant_users_updated_at BEFORE UPDATE ON tenant_users
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_services_updated_at BEFORE UPDATE ON services
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_scheduling_configs_updated_at BEFORE UPDATE ON scheduling_configs
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_appointments_updated_at BEFORE UPDATE ON appointments
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_whatsapp_sessions_updated_at BEFORE UPDATE ON whatsapp_sessions
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ============================================================================
-- ROW LEVEL SECURITY (multi-tenant isolation)
-- App connects with a role that sets: SET app.current_tenant_id = '<uuid>';
-- per request/session. Superuser / migration roles bypass RLS as usual.
-- ============================================================================

ALTER TABLE tenant_users        ENABLE ROW LEVEL SECURITY;
ALTER TABLE services            ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduling_configs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE holidays            ENABLE ROW LEVEL SECURITY;
ALTER TABLE blocked_times       ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments        ENABLE ROW LEVEL SECURITY;
ALTER TABLE available_slots     ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_sessions   ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tenant_users
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation ON services
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation ON scheduling_configs
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation ON holidays
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation ON blocked_times
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation ON appointments
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation ON available_slots
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation ON whatsapp_sessions
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
