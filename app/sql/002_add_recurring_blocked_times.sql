-- ============================================================================
-- WhatsApp Appointment Booking Engine
-- Migration 002: Add recurring_blocked_times table
-- Purpose: Support daily recurring blocked time slots (lunch, meetings, etc.)
--
-- This is an INCREMENTAL migration - run this on an existing schema that
-- already has the base tables (tenants, services, scheduling_configs, etc.)
--
-- To run in Neon:
-- 1. Connect to your database
-- 2. Paste this entire script
-- 3. Click "Execute query" or run: psql -d <connection_string> < 002_add_recurring_blocked_times.sql
-- ============================================================================

-- ============================================================================
-- Create the recurring_blocked_times table
-- ============================================================================

CREATE TABLE IF NOT EXISTS recurring_blocked_times (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Time slot definition (wall-clock time in tenant's timezone)
    start_time      VARCHAR(5) NOT NULL,                 -- HH:MM format, e.g. "12:00"
    end_time        VARCHAR(5) NOT NULL,                 -- HH:MM format, e.g. "13:00"

    -- Which days of the week this applies to (bitmask: bit0=Sun..bit6=Sat)
    -- 1=Sun, 2=Mon, 4=Tue, 8=Wed, 16=Thu, 32=Fri, 64=Sat
    -- Default 127 = all days; 62 = Mon-Fri (2+4+8+16+32); 65 = weekends (1+64)
    days_of_week    SMALLINT NOT NULL DEFAULT 127,

    -- Optional date boundaries for the recurrence (NULL = no limit)
    start_date      DATE,
    end_date        DATE,

    -- Descriptive reason (e.g., "Lunch break", "Daily standup", "Training")
    reason          VARCHAR(255),

    -- Whether this recurring block is currently active/enabled
    active          BOOLEAN NOT NULL DEFAULT true,

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Constraints
    CONSTRAINT chk_recurring_blocked_times_start_before_end
        CHECK (start_time < end_time),
    CONSTRAINT chk_recurring_blocked_times_days_valid
        CHECK (days_of_week >= 0 AND days_of_week <= 127),
    CONSTRAINT chk_recurring_blocked_times_date_range
        CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date)
);

-- ============================================================================
-- Create indexes for efficient queries
-- ============================================================================

-- Index for listing recurring blocks for a tenant
CREATE INDEX IF NOT EXISTS idx_recurring_blocked_times_tenant
    ON recurring_blocked_times(tenant_id);

-- Index for querying active blocks (used during slot calculation)
CREATE INDEX IF NOT EXISTS idx_recurring_blocked_times_active
    ON recurring_blocked_times(tenant_id, active)
    WHERE active = true;

-- Index for querying blocks with date boundaries
CREATE INDEX IF NOT EXISTS idx_recurring_blocked_times_dates
    ON recurring_blocked_times(tenant_id, start_date, end_date)
    WHERE active = true;

-- ============================================================================
-- Enable Row Level Security for multi-tenant isolation
-- ============================================================================

ALTER TABLE recurring_blocked_times ENABLE ROW LEVEL SECURITY;

-- Create RLS policy (same pattern as other tables)
CREATE POLICY tenant_isolation ON recurring_blocked_times
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- ============================================================================
-- Add auto-update trigger for updated_at column
-- ============================================================================

-- Check if the trigger function exists (it should from the initial schema)
-- If not, create it:
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create the trigger for this table
CREATE TRIGGER IF NOT EXISTS trg_recurring_blocked_times_updated_at
    BEFORE UPDATE ON recurring_blocked_times
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ============================================================================
-- Verification Query (run this to confirm everything is set up)
-- ============================================================================
/*
SELECT 
    table_name,
    EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'recurring_blocked_times'
    ) as table_exists
FROM information_schema.tables
WHERE table_name = 'recurring_blocked_times';

-- Should return: recurring_blocked_times | t
*/

-- ============================================================================
-- SAMPLE DATA (optional — delete in production)
-- ============================================================================

-- Example: Add lunch break for your first tenant
/*
INSERT INTO recurring_blocked_times (
    tenant_id, 
    start_time, 
    end_time, 
    days_of_week, 
    reason, 
    active
)
SELECT 
    id,
    '12:00',
    '13:00',
    127,  -- every day
    'Lunch break',
    true
FROM tenants
LIMIT 1;

-- Verify insertion:
SELECT * FROM recurring_blocked_times ORDER BY created_at DESC LIMIT 5;
*/

-- ============================================================================
-- Migration Complete
-- ============================================================================
-- The recurring_blocked_times table is now ready for use.
-- 
-- Next steps:
-- 1. Update your Python models (app/models/scheduling.py)
-- 2. Update your Pydantic schemas (app/schemas/scheduling.py)
-- 3. Update slot calculation logic (app/services/slots.py)
-- 4. Add router endpoints (app/routers/scheduling.py)
-- 5. Update your admin dashboard UI
-- 6. Test via API
--
-- See IMPLEMENTATION_GUIDE.md for full details.
-- ============================================================================

-- ============================================================================
-- Enable subscription_enabled
-- ============================================================================

ALTER TABLE tenants
    ADD COLUMN subscription_enabled BOOLEAN NOT NULL DEFAULT true;
	
	
-- 1. Add the column (defaults to false for every existing row)
ALTER TABLE tenant_users
    ADD COLUMN is_owner BOOLEAN NOT NULL DEFAULT false;

