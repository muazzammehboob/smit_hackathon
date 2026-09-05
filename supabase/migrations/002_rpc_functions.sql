-- ============================================================================
-- Migration: 002_rpc_functions.sql
-- Flight Management System (FMS) - Supabase PostgreSQL Schema
-- Stage 1B: 5 Atomic ACID RPC Transaction Functions
-- ============================================================================

-- ============================================================================
-- 1. RPC: hold_seat
-- Atomically row-locks seat, verifies AVAILABLE, inserts seat_holds, marks seat HELD
-- ============================================================================
CREATE OR REPLACE FUNCTION hold_seat(
    p_flight_id UUID,
    p_seat_id UUID,
    p_session_id TEXT,
    p_hold_minutes INT DEFAULT 10
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_seat RECORD;
    v_flight RECORD;
    v_hold_id UUID;
    v_effective_minutes INT;
BEGIN
    -- Validate parameters
    IF p_flight_id IS NULL OR p_seat_id IS NULL OR p_session_id IS NULL OR TRIM(p_session_id) = '' THEN
        RAISE EXCEPTION 'Invalid parameters: flight_id, seat_id, and session_id are required'
            USING ERRCODE = '22023';
    END IF;

    v_effective_minutes := COALESCE(NULLIF(p_hold_minutes, 0), 10);
    IF v_effective_minutes < 1 OR v_effective_minutes > 120 THEN
        RAISE EXCEPTION 'Hold duration must be between 1 and 120 minutes'
            USING ERRCODE = '22023';
    END IF;

    -- Verify flight exists and is active
    SELECT id, status INTO v_flight
    FROM flights
    WHERE id = p_flight_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Flight % not found', p_flight_id
            USING ERRCODE = 'P0002';
    END IF;

    IF v_flight.status IN ('CANCELLED', 'DEPARTED', 'ARRIVED') THEN
        RAISE EXCEPTION 'Cannot hold seat on flight % with status %', p_flight_id, v_flight.status
            USING ERRCODE = '55000';
    END IF;

    -- Atomically lock the seat row for update
    SELECT id, flight_id, seat_number, seat_class, status INTO v_seat
    FROM flight_seats
    WHERE id = p_seat_id AND flight_id = p_flight_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Seat % not found on flight %', p_seat_id, p_flight_id
            USING ERRCODE = 'P0002';
    END IF;

    IF v_seat.status != 'AVAILABLE' THEN
        RAISE EXCEPTION 'Seat % (%) is not available (currently %)', v_seat.seat_number, v_seat.id, v_seat.status
            USING ERRCODE = '55P03';
    END IF;

    -- Create temporary reservation hold
    INSERT INTO seat_holds (
        flight_id,
        seat_id,
        session_id,
        status,
        expires_at,
        created_at
    ) VALUES (
        p_flight_id,
        p_seat_id,
        p_session_id,
        'HELD',
        NOW() + (v_effective_minutes || ' minutes')::INTERVAL,
        NOW()
    ) RETURNING id INTO v_hold_id;

    -- Update seat status to HELD
    UPDATE flight_seats
    SET status = 'HELD'
    WHERE id = p_seat_id;

    RETURN v_hold_id;
END;
$$;

-- ============================================================================
-- 2. RPC: confirm_booking
-- Validates hold, converts to confirmed booking, increments booked_seats
-- ============================================================================
CREATE OR REPLACE FUNCTION confirm_booking(
    p_hold_id UUID,
    p_passenger_name TEXT,
    p_passenger_email TEXT,
    p_fare_class fare_class_enum,
    p_fare_cents INT,
    p_ip TEXT DEFAULT '127.0.0.1'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_hold RECORD;
    v_seat RECORD;
    v_class RECORD;
    v_booking RECORD;
    v_first_name TEXT;
    v_last_name TEXT;
BEGIN
    -- Validate parameters
    IF p_hold_id IS NULL THEN
        RAISE EXCEPTION 'Hold ID is required' USING ERRCODE = '22023';
    END IF;

    IF p_passenger_name IS NULL OR TRIM(p_passenger_name) = '' THEN
        RAISE EXCEPTION 'Passenger name is required' USING ERRCODE = '22023';
    END IF;

    IF p_passenger_email IS NULL OR TRIM(p_passenger_email) = '' THEN
        RAISE EXCEPTION 'Passenger email is required' USING ERRCODE = '22023';
    END IF;

    IF p_fare_cents IS NULL OR p_fare_cents < 0 THEN
        RAISE EXCEPTION 'Fare in cents must be non-negative' USING ERRCODE = '22023';
    END IF;

    -- Lock the hold record
    SELECT * INTO v_hold
    FROM seat_holds
    WHERE id = p_hold_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Hold % not found', p_hold_id USING ERRCODE = 'P0002';
    END IF;

    IF v_hold.status != 'HELD' THEN
        RAISE EXCEPTION 'Hold % is not active (current status: %)', p_hold_id, v_hold.status
            USING ERRCODE = '55000';
    END IF;

    -- Check TTL expiration
    IF v_hold.expires_at < NOW() THEN
        -- Mark expired and release seat
        UPDATE seat_holds SET status = 'EXPIRED' WHERE id = p_hold_id;
        UPDATE flight_seats SET status = 'AVAILABLE' WHERE id = v_hold.seat_id;
        RAISE EXCEPTION 'Seat hold % expired at %', p_hold_id, v_hold.expires_at
            USING ERRCODE = '55000';
    END IF;

    -- Row-lock physical seat
    SELECT * INTO v_seat
    FROM flight_seats
    WHERE id = v_hold.seat_id
    FOR UPDATE;

    -- Row-lock inventory class for capacity limit invariant check
    SELECT * INTO v_class
    FROM flight_classes
    WHERE flight_id = v_hold.flight_id AND seat_class = v_seat.seat_class
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Class inventory record not found for class %', v_seat.seat_class
            USING ERRCODE = 'P0002';
    END IF;

    -- Invariant check: cannot exceed capacity * (1 + overbooking_pct/100)
    IF v_class.booked_seats + 1 > (v_class.capacity * (1 + v_class.overbooking_pct / 100.0)) THEN
        RAISE EXCEPTION 'Capacity exceeded for class % on flight %', v_seat.seat_class, v_hold.flight_id
            USING ERRCODE = '54000';
    END IF;

    -- Parse passenger name into first and last name
    v_first_name := split_part(TRIM(p_passenger_name), ' ', 1);
    v_last_name := COALESCE(
        NULLIF(TRIM(substr(TRIM(p_passenger_name), length(v_first_name) + 1)), ''),
        v_first_name
    );

    -- Insert confirmed booking (PNR auto-generated via trigger)
    INSERT INTO bookings (
        flight_id,
        seat_id,
        passenger_name,
        passenger_email,
        fare_class,
        fare_paid_cents,
        status,
        ip_address,
        created_at,
        updated_at
    ) VALUES (
        v_hold.flight_id,
        v_hold.seat_id,
        TRIM(p_passenger_name),
        TRIM(p_passenger_email),
        p_fare_class,
        p_fare_cents,
        'CONFIRMED',
        COALESCE(NULLIF(TRIM(p_ip), ''), '127.0.0.1'),
        NOW(),
        NOW()
    ) RETURNING * INTO v_booking;

    -- Register passenger manifest entry
    INSERT INTO passengers (
        booking_id,
        flight_id,
        seat_id,
        first_name,
        last_name,
        email,
        created_at
    ) VALUES (
        v_booking.id,
        v_hold.flight_id,
        v_hold.seat_id,
        v_first_name,
        v_last_name,
        TRIM(p_passenger_email),
        NOW()
    );

    -- Update seat to BOOKED and link booking_id
    UPDATE flight_seats
    SET status = 'BOOKED',
        booking_id = v_booking.id
    WHERE id = v_hold.seat_id;

    -- Increment booked seats counter in flight_classes
    UPDATE flight_classes
    SET booked_seats = booked_seats + 1
    WHERE id = v_class.id;

    -- Mark seat hold as CONVERTED
    UPDATE seat_holds
    SET status = 'CONVERTED'
    WHERE id = p_hold_id;

    -- Return JSONB booking confirmation
    RETURN jsonb_build_object(
        'booking_id', v_booking.id,
        'pnr', v_booking.pnr,
        'flight_id', v_booking.flight_id,
        'seat_id', v_booking.seat_id,
        'seat_number', v_seat.seat_number,
        'seat_class', v_seat.seat_class,
        'passenger_name', v_booking.passenger_name,
        'passenger_email', v_booking.passenger_email,
        'fare_class', v_booking.fare_class,
        'fare_paid_cents', v_booking.fare_paid_cents,
        'status', v_booking.status,
        'created_at', v_booking.created_at
    );
END;
$$;

-- ============================================================================
-- 3. RPC: cancel_booking
-- Releases booking, applies fare rules (Basic=0%, Flex=90%, First=100%), decrements booked_seats
-- ============================================================================
CREATE OR REPLACE FUNCTION cancel_booking(
    p_booking_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_booking RECORD;
    v_seat RECORD;
    v_refund_amount_cents INT := 0;
    v_penalty_cents INT := 0;
    v_refund_type VARCHAR(20) := 'CASH';
    v_refund_status VARCHAR(20) := 'PENDING';
    v_refund_id UUID;
    v_reason TEXT;
BEGIN
    IF p_booking_id IS NULL THEN
        RAISE EXCEPTION 'Booking ID is required' USING ERRCODE = '22023';
    END IF;

    -- Atomically lock the booking
    SELECT * INTO v_booking
    FROM bookings
    WHERE id = p_booking_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Booking % not found', p_booking_id USING ERRCODE = 'P0002';
    END IF;

    IF v_booking.status = 'CANCELLED' THEN
        RAISE EXCEPTION 'Booking % is already cancelled', p_booking_id USING ERRCODE = '55000';
    END IF;

    IF v_booking.status = 'REFUNDED' THEN
        RAISE EXCEPTION 'Booking % is already refunded', p_booking_id USING ERRCODE = '55000';
    END IF;

    -- Calculate refund based on fare class and disruption status
    -- Airline disruption override (FR-4.3): 100% refund regardless of fare class
    IF v_booking.schedule_disrupted = TRUE THEN
        v_refund_amount_cents := v_booking.fare_paid_cents;
        v_penalty_cents := 0;
        v_refund_type := 'CASH';
        v_refund_status := 'PENDING';
        v_reason := 'Schedule disruption override (100% refund)';
    ELSE
        CASE v_booking.fare_class
            WHEN 'BASIC_ECONOMY' THEN
                -- Basic Economy: 0% refund, 100% penalty
                v_refund_amount_cents := 0;
                v_penalty_cents := v_booking.fare_paid_cents;
                v_refund_type := 'NONE';
                v_refund_status := 'COMPLETED';
                v_reason := 'Basic Economy non-refundable cancellation ($0 refund)';
            WHEN 'FLEXIBLE' THEN
                -- Flexible: 90% refund, 10% penalty fee
                v_penalty_cents := ROUND(v_booking.fare_paid_cents * 0.10)::INT;
                v_refund_amount_cents := v_booking.fare_paid_cents - v_penalty_cents;
                v_refund_type := 'CASH';
                v_refund_status := 'PENDING';
                v_reason := 'Flexible fare standard cancellation (90% refund, 10% processing fee)';
            WHEN 'PREMIUM_FIRST' THEN
                -- Premium First: 100% refund, 0% penalty
                v_refund_amount_cents := v_booking.fare_paid_cents;
                v_penalty_cents := 0;
                v_refund_type := 'CASH';
                v_refund_status := 'PENDING';
                v_reason := 'Premium First standard cancellation (100% full refund)';
        END CASE;
    END IF;

    -- Free the physical seat and decrement inventory
    IF v_booking.seat_id IS NOT NULL THEN
        SELECT * INTO v_seat
        FROM flight_seats
        WHERE id = v_booking.seat_id
        FOR UPDATE;

        -- Reset physical seat to AVAILABLE
        UPDATE flight_seats
        SET status = 'AVAILABLE',
            booking_id = NULL
        WHERE id = v_booking.seat_id;

        -- Decrement booked_seats in flight_classes safely
        UPDATE flight_classes
        SET booked_seats = GREATEST(0, booked_seats - 1)
        WHERE flight_id = v_booking.flight_id AND seat_class = v_seat.seat_class;
    END IF;

    -- Record refund transaction in refunds ledger
    INSERT INTO refunds (
        booking_id,
        amount_cents,
        penalty_fee_cents,
        refund_type,
        status,
        reason,
        processed_at,
        created_at
    ) VALUES (
        v_booking.id,
        v_refund_amount_cents,
        v_penalty_cents,
        v_refund_type,
        v_refund_status,
        v_reason,
        CASE WHEN v_refund_amount_cents = 0 THEN NOW() ELSE NULL END,
        NOW()
    ) RETURNING id INTO v_refund_id;

    -- Update booking status to CANCELLED
    UPDATE bookings
    SET status = 'CANCELLED',
        updated_at = NOW()
    WHERE id = p_booking_id;

    RETURN jsonb_build_object(
        'booking_id', v_booking.id,
        'pnr', v_booking.pnr,
        'flight_id', v_booking.flight_id,
        'fare_class', v_booking.fare_class,
        'fare_paid_cents', v_booking.fare_paid_cents,
        'refund_amount_cents', v_refund_amount_cents,
        'penalty_fee_cents', v_penalty_cents,
        'refund_type', v_refund_type,
        'refund_status', v_refund_status,
        'refund_id', v_refund_id,
        'updated_booking_status', 'CANCELLED'
    );
END;
$$;

-- ============================================================================
-- 4. RPC: promote_waitlist
-- Grabs top waitlisted passenger with SKIP LOCKED, holds available seat, promotes
-- ============================================================================
CREATE OR REPLACE FUNCTION promote_waitlist(
    p_flight_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_waitlist RECORD;
    v_seat RECORD;
    v_hold_id UUID;
    v_deadline TIMESTAMPTZ;
BEGIN
    IF p_flight_id IS NULL THEN
        RAISE EXCEPTION 'Flight ID is required' USING ERRCODE = '22023';
    END IF;

    -- Find the highest priority waiting passenger on this flight using SKIP LOCKED
    SELECT * INTO v_waitlist
    FROM waitlist
    WHERE flight_id = p_flight_id AND status = 'WAITING'
    ORDER BY priority_score DESC
    LIMIT 1
    FOR UPDATE SKIP LOCKED;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'promoted', FALSE,
            'message', 'No eligible waitlist entries found for promotion'
        );
    END IF;

    -- Find an available physical seat in the requested class using SKIP LOCKED
    SELECT * INTO v_seat
    FROM flight_seats
    WHERE flight_id = p_flight_id
      AND seat_class = v_waitlist.requested_class
      AND status = 'AVAILABLE'
    ORDER BY seat_number ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'promoted', FALSE,
            'waitlist_id', v_waitlist.id,
            'requested_class', v_waitlist.requested_class,
            'message', 'No available physical seat found in requested class'
        );
    END IF;

    -- Define 2-hour promotion claim deadline
    v_deadline := NOW() + INTERVAL '2 hours';

    -- Create 2-hour temporary seat hold
    INSERT INTO seat_holds (
        flight_id,
        seat_id,
        session_id,
        status,
        expires_at,
        created_at
    ) VALUES (
        p_flight_id,
        v_seat.id,
        'waitlist-promo-' || v_waitlist.id,
        'HELD',
        v_deadline,
        NOW()
    ) RETURNING id INTO v_hold_id;

    -- Transition physical seat to HELD
    UPDATE flight_seats
    SET status = 'HELD'
    WHERE id = v_seat.id;

    -- Transition waitlist record to PROMOTED with hold and deadline
    UPDATE waitlist
    SET status = 'PROMOTED',
        hold_id = v_hold_id,
        claim_deadline = v_deadline
    WHERE id = v_waitlist.id;

    RETURN jsonb_build_object(
        'promoted', TRUE,
        'waitlist_id', v_waitlist.id,
        'passenger_name', v_waitlist.passenger_name,
        'passenger_email', v_waitlist.passenger_email,
        'loyalty_tier', v_waitlist.loyalty_tier,
        'requested_class', v_waitlist.requested_class,
        'priority_score', v_waitlist.priority_score,
        'flight_id', p_flight_id,
        'seat_id', v_seat.id,
        'seat_number', v_seat.seat_number,
        'hold_id', v_hold_id,
        'claim_deadline', v_deadline
    );
END;
$$;

-- ============================================================================
-- 5. RPC: sweep_expired_holds
-- Finds expired holds (expires_at < NOW() AND status = 'HELD'), releases seats, marks EXPIRED
-- ============================================================================
CREATE OR REPLACE FUNCTION sweep_expired_holds()
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_released_count INT := 0;
BEGIN
    -- Atomic CTE: lock expired holds, release seats, transition holds to EXPIRED
    WITH expired_holds AS (
        SELECT id, flight_id, seat_id
        FROM seat_holds
        WHERE expires_at < NOW() AND status = 'HELD'
        FOR UPDATE
    ),
    released_seats AS (
        UPDATE flight_seats fs
        SET status = 'AVAILABLE'
        FROM expired_holds eh
        WHERE fs.id = eh.seat_id AND fs.status = 'HELD'
        RETURNING fs.id
    ),
    closed_holds AS (
        UPDATE seat_holds sh
        SET status = 'EXPIRED'
        FROM expired_holds eh
        WHERE sh.id = eh.id
        RETURNING sh.id
    ),
    expired_waitlist_entries AS (
        UPDATE waitlist w
        SET status = 'EXPIRED_UNCLAIMED'
        FROM expired_holds eh
        WHERE w.hold_id = eh.id AND w.status = 'PROMOTED'
        RETURNING w.id
    )
    SELECT COUNT(*)::INT INTO v_released_count
    FROM closed_holds;

    RETURN v_released_count;
END;
$$;
