-- ============================================================================
-- Migration: 001_complete_schema.sql
-- Flight Management System (FMS) - Supabase PostgreSQL Schema
-- Stage 1A: Complete Schema, Enums, Constraints, Indexes, and PNR Trigger
-- ============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- 1. CUSTOM ENUMS (6 Enums)
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'flight_status_enum') THEN
        CREATE TYPE flight_status_enum AS ENUM (
            'SCHEDULED',
            'BOARDING',
            'DEPARTED',
            'ARRIVED',
            'CANCELLED',
            'DELAYED'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'seat_class_enum') THEN
        CREATE TYPE seat_class_enum AS ENUM (
            'FIRST',
            'BUSINESS',
            'ECONOMY'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'seat_status_enum') THEN
        CREATE TYPE seat_status_enum AS ENUM (
            'AVAILABLE',
            'HELD',
            'BOOKED',
            'BLOCKED'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'booking_status_enum') THEN
        CREATE TYPE booking_status_enum AS ENUM (
            'HELD',
            'CONFIRMED',
            'CANCELLED',
            'REFUNDED',
            'SUSPECTED_FRAUD'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'fare_class_enum') THEN
        CREATE TYPE fare_class_enum AS ENUM (
            'BASIC_ECONOMY',
            'FLEXIBLE',
            'PREMIUM_FIRST'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'waitlist_status_enum') THEN
        CREATE TYPE waitlist_status_enum AS ENUM (
            'WAITING',
            'PROMOTED',
            'EXPIRED_UNCLAIMED',
            'CONVERTED',
            'CANCELLED'
        );
    END IF;
END $$;

-- ============================================================================
-- 2. ALL 16 TABLES & INVARIANT CONSTRAINTS
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table 1: flights
-- Invariants: origin != destination, arrival > departure, capacity > 0
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_number VARCHAR(10) NOT NULL,
    origin_airport VARCHAR(3) NOT NULL,
    destination_airport VARCHAR(3) NOT NULL,
    departure_time TIMESTAMPTZ NOT NULL,
    arrival_time TIMESTAMPTZ NOT NULL,
    origin_tz VARCHAR(50) NOT NULL DEFAULT 'UTC',
    status flight_status_enum NOT NULL DEFAULT 'SCHEDULED',
    total_capacity INT NOT NULL,
    schedule_version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT check_airports_distinct CHECK (origin_airport != destination_airport),
    CONSTRAINT check_arrival_after_departure CHECK (arrival_time > departure_time),
    CONSTRAINT check_positive_capacity CHECK (total_capacity > 0),
    CONSTRAINT unique_flight_day UNIQUE (flight_number, origin_airport, departure_time)
);

-- ----------------------------------------------------------------------------
-- Table 2: flight_classes (Inventory breakdown and overbooking limits)
-- Invariants: capacity >= 0, booked_seats >= 0, booked_seats <= capacity * (1 + overbooking_pct/100)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flight_classes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    seat_class seat_class_enum NOT NULL,
    capacity INT NOT NULL,
    booked_seats INT NOT NULL DEFAULT 0,
    base_price_cents INT NOT NULL DEFAULT 0,
    overbooking_pct INT NOT NULL DEFAULT 0,
    
    CONSTRAINT check_class_capacity_positive CHECK (capacity >= 0),
    CONSTRAINT check_booked_seats_positive CHECK (booked_seats >= 0),
    CONSTRAINT check_inventory_limit CHECK (booked_seats <= (capacity * (1 + overbooking_pct / 100.0))),
    CONSTRAINT check_overbooking_pct_non_negative CHECK (overbooking_pct >= 0),
    CONSTRAINT unique_flight_class UNIQUE (flight_id, seat_class)
);

-- ----------------------------------------------------------------------------
-- Table 3: flight_seats (Physical seat layout per aircraft)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flight_seats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    seat_number VARCHAR(10) NOT NULL,
    seat_class seat_class_enum NOT NULL,
    status seat_status_enum NOT NULL DEFAULT 'AVAILABLE',
    booking_id UUID, -- circular reference resolved after bookings table creation
    
    CONSTRAINT unique_seat_per_flight UNIQUE (flight_id, seat_number)
);

-- ----------------------------------------------------------------------------
-- Table 4: seat_holds (TTL reservation locks to prevent overselling)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS seat_holds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    seat_id UUID NOT NULL REFERENCES flight_seats(id) ON DELETE CASCADE,
    session_id VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'HELD',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT check_seat_hold_status CHECK (status IN ('HELD', 'CONVERTED', 'EXPIRED', 'RELEASED'))
);

-- ----------------------------------------------------------------------------
-- Table 5: bookings (Primary ledger of truth)
-- Restricts flight deletion if confirmed bookings exist
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bookings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pnr VARCHAR(10) NOT NULL UNIQUE,
    parent_pnr VARCHAR(10),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE RESTRICT,
    seat_id UUID REFERENCES flight_seats(id) ON DELETE SET NULL,
    user_id UUID,
    passenger_name VARCHAR(150) NOT NULL,
    passenger_email VARCHAR(150) NOT NULL,
    fare_class fare_class_enum NOT NULL,
    fare_paid_cents INT NOT NULL CHECK (fare_paid_cents >= 0),
    status booking_status_enum NOT NULL DEFAULT 'CONFIRMED',
    ip_address VARCHAR(45) NOT NULL DEFAULT '127.0.0.1',
    schedule_disrupted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Circular FK resolution: link flight_seats.booking_id -> bookings.id
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE constraint_name = 'fk_flight_seats_booking' AND table_name = 'flight_seats'
    ) THEN
        ALTER TABLE flight_seats 
        ADD CONSTRAINT fk_flight_seats_booking 
        FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE SET NULL;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- Table 6: passengers (Passenger manifest records)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS passengers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    flight_id UUID REFERENCES flights(id) ON DELETE CASCADE,
    seat_id UUID REFERENCES flight_seats(id) ON DELETE SET NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(150) NOT NULL,
    phone VARCHAR(50),
    passport_number VARCHAR(50),
    loyalty_tier VARCHAR(20) NOT NULL DEFAULT 'GENERAL',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Table 7: waitlist (Deterministic priority standby queue)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS waitlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    passenger_name VARCHAR(150) NOT NULL,
    passenger_email VARCHAR(150) NOT NULL,
    loyalty_tier VARCHAR(20) NOT NULL DEFAULT 'GENERAL',
    requested_class seat_class_enum NOT NULL,
    priority_score BIGINT NOT NULL,
    status waitlist_status_enum NOT NULL DEFAULT 'WAITING',
    hold_id UUID REFERENCES seat_holds(id) ON DELETE SET NULL,
    claim_deadline TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Table 8: refunds (Financial reconciliation ledger)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    amount_cents INT NOT NULL CHECK (amount_cents >= 0),
    penalty_fee_cents INT NOT NULL DEFAULT 0 CHECK (penalty_fee_cents >= 0),
    refund_type VARCHAR(20) NOT NULL DEFAULT 'CASH',
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    reason TEXT,
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT check_refund_status CHECK (status IN ('PENDING', 'COMPLETED', 'FAILED', 'REJECTED'))
);

-- ----------------------------------------------------------------------------
-- Table 9: travel_credits (Vouchers issued for cancellations)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS travel_credits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    voucher_code VARCHAR(20) NOT NULL UNIQUE,
    passenger_email VARCHAR(150) NOT NULL,
    booking_id UUID REFERENCES bookings(id) ON DELETE SET NULL,
    amount_cents INT NOT NULL CHECK (amount_cents > 0),
    balance_cents INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '365 days'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT check_balance_validity CHECK (balance_cents >= 0 AND balance_cents <= amount_cents),
    CONSTRAINT check_voucher_status CHECK (status IN ('ACTIVE', 'REDEEMED', 'EXPIRED'))
);

-- ----------------------------------------------------------------------------
-- Table 10: idempotency_records (Anti-duplicate request cache)
-- Supports both FastAPI response models and data contracts
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idempotency_records (
    key VARCHAR(100) PRIMARY KEY,
    endpoint VARCHAR(100) NOT NULL DEFAULT '',
    response_code INT NOT NULL DEFAULT 200,
    status_code INT NOT NULL DEFAULT 200,
    response_body JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Table 11: admin_audit_logs (Immutable operations audit trail)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id VARCHAR(100) NOT NULL DEFAULT 'system',
    action_type VARCHAR(50) NOT NULL,
    resource_id VARCHAR(100),
    previous_state JSONB DEFAULT '{}'::jsonb,
    new_state JSONB DEFAULT '{}'::jsonb,
    ip_address VARCHAR(45) NOT NULL DEFAULT '127.0.0.1',
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Table 12: notification_logs (Email and message delivery ledger)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id UUID REFERENCES bookings(id) ON DELETE SET NULL,
    type VARCHAR(50) NOT NULL,
    recipient VARCHAR(150),
    status VARCHAR(20) NOT NULL DEFAULT 'SENT',
    details JSONB DEFAULT '{}'::jsonb,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Table 13: daily_ops_reports (Automated revenue and load factor rollups)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_ops_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date DATE NOT NULL UNIQUE,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    total_revenue_cents BIGINT NOT NULL DEFAULT 0,
    total_bookings INT NOT NULL DEFAULT 0,
    avg_load_factor_pct NUMERIC(5, 2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Table 14: support_draft_approvals (Human-in-the-Loop AI RAG Gate)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS support_draft_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pnr VARCHAR(10) NOT NULL,
    customer_email VARCHAR(150) NOT NULL,
    inquiry_text TEXT NOT NULL,
    retrieved_policy_snippet TEXT NOT NULL DEFAULT '',
    draft_response TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    reviewer_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    
    CONSTRAINT check_support_draft_status CHECK (status IN ('PENDING', 'APPROVED', 'EDITED_APPROVED', 'REJECTED'))
);

-- ----------------------------------------------------------------------------
-- Table 15: ops_escalations (Security and automated alert incidents)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ops_escalations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reason VARCHAR(100) NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'MEDIUM',
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    resolved_by VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT check_escalation_severity CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CONSTRAINT check_escalation_status CHECK (status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED'))
);

-- ----------------------------------------------------------------------------
-- Table 16: price_alert_subscriptions (Passenger route alert subscriptions)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_alert_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_email VARCHAR(150) NOT NULL,
    origin_airport VARCHAR(3) NOT NULL,
    destination_airport VARCHAR(3) NOT NULL,
    target_price_cents INT NOT NULL CHECK (target_price_cents > 0),
    seat_class seat_class_enum NOT NULL DEFAULT 'ECONOMY',
    last_alert_sent_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 3. PERFORMANCE B-TREE INDEXES
-- ============================================================================

-- B-Tree index for active seat holds lookup and TTL sweep
CREATE INDEX IF NOT EXISTS idx_seat_holds_active 
ON seat_holds (flight_id, status, expires_at);

-- B-Tree index for customer booking lookup via PNR and email
CREATE INDEX IF NOT EXISTS idx_bookings_lookup 
ON bookings (pnr, passenger_email);

-- B-Tree index for real-time bot / burst booking fraud scanning
CREATE INDEX IF NOT EXISTS idx_bookings_fraud_scan 
ON bookings (ip_address, created_at);

-- B-Tree index for priority waitlist queue resolution
CREATE INDEX IF NOT EXISTS idx_waitlist_priority 
ON waitlist (flight_id, requested_class, status, priority_score DESC);

-- Supporting indexes for high-frequency queries
CREATE INDEX IF NOT EXISTS idx_flight_seats_lookup 
ON flight_seats (flight_id, seat_class, status);

CREATE INDEX IF NOT EXISTS idx_flight_classes_flight 
ON flight_classes (flight_id);

CREATE INDEX IF NOT EXISTS idx_passengers_booking 
ON passengers (booking_id);

CREATE INDEX IF NOT EXISTS idx_refunds_booking 
ON refunds (booking_id);

CREATE INDEX IF NOT EXISTS idx_support_drafts_status 
ON support_draft_approvals (status);

-- ============================================================================
-- 4. PNR AUTO-GENERATION TRIGGER FUNCTION & TRIGGER
-- Generates random 6-character alphanumeric uppercase PNR excluding 0, O, 1, I
-- Alphabet: ABCDEFGHJKLMNPQRSTUVWXYZ23456789 (32 characters)
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_booking_pnr()
RETURNS TRIGGER AS $$
DECLARE
    v_chars TEXT := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    v_pnr TEXT;
    v_exists BOOLEAN;
    v_i INT;
    v_max_attempts INT := 100;
    v_attempt INT := 0;
BEGIN
    -- Only auto-generate if PNR was not explicitly supplied
    IF NEW.pnr IS NULL OR TRIM(NEW.pnr) = '' THEN
        LOOP
            v_pnr := '';
            v_attempt := v_attempt + 1;
            
            -- Generate 6 random characters from unambiguous alphabet
            FOR v_i IN 1..6 LOOP
                v_pnr := v_pnr || substr(v_chars, floor(random() * length(v_chars) + 1)::INT, 1);
            END LOOP;
            
            -- Verify uniqueness against bookings ledger
            SELECT EXISTS(SELECT 1 FROM bookings WHERE pnr = v_pnr) INTO v_exists;
            EXIT WHEN NOT v_exists;
            
            IF v_attempt >= v_max_attempts THEN
                RAISE EXCEPTION 'Failed to generate a unique PNR code after % attempts', v_max_attempts;
            END IF;
        END LOOP;
        
        NEW.pnr := v_pnr;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_generate_booking_pnr ON bookings;
CREATE TRIGGER trg_generate_booking_pnr
BEFORE INSERT ON bookings
FOR EACH ROW
EXECUTE FUNCTION generate_booking_pnr();
