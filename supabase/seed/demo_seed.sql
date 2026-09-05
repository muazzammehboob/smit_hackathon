-- ============================================================================
-- Seed Script: demo_seed.sql
-- Flight Management System (FMS) - Supabase PostgreSQL Seed Data
-- Stage 1C: 3 Flights, 330 Physical Seats, 5 Bookings, 2 Waitlists, 1 Hold
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- Cleanup any existing demo records to ensure safe, idempotent execution
-- ----------------------------------------------------------------------------
DELETE FROM bookings WHERE flight_id IN (
    '11111111-1111-1111-1111-111111111111'::UUID,
    '22222222-2222-2222-2222-222222222222'::UUID,
    '33333333-3333-3333-3333-333333333333'::UUID
);

DELETE FROM flights WHERE id IN (
    '11111111-1111-1111-1111-111111111111'::UUID,
    '22222222-2222-2222-2222-222222222222'::UUID,
    '33333333-3333-3333-3333-333333333333'::UUID
);

-- ============================================================================
-- 1. INSERT 3 FLIGHTS
-- ============================================================================

-- Flight 1: BA101 (LHR -> DXB, 100 seats: 20 First, 30 Business, 50 Economy)
INSERT INTO flights (
    id,
    flight_number,
    origin_airport,
    destination_airport,
    departure_time,
    arrival_time,
    origin_tz,
    status,
    total_capacity,
    schedule_version,
    created_at
) VALUES (
    '11111111-1111-1111-1111-111111111111'::UUID,
    'BA101',
    'LHR',
    'DXB',
    NOW() + INTERVAL '2 days' + INTERVAL '5 hours',
    NOW() + INTERVAL '2 days' + INTERVAL '12 hours',
    'Europe/London',
    'SCHEDULED',
    100,
    1,
    NOW()
);

-- Flight 2: PK202 (KHI -> DXB, 80 seats: 10 First, 20 Business, 50 Economy)
INSERT INTO flights (
    id,
    flight_number,
    origin_airport,
    destination_airport,
    departure_time,
    arrival_time,
    origin_tz,
    status,
    total_capacity,
    schedule_version,
    created_at
) VALUES (
    '22222222-2222-2222-2222-222222222222'::UUID,
    'PK202',
    'KHI',
    'DXB',
    NOW() + INTERVAL '3 days' + INTERVAL '8 hours',
    NOW() + INTERVAL '3 days' + INTERVAL '10 hours 30 minutes',
    'Asia/Karachi',
    'SCHEDULED',
    80,
    1,
    NOW()
);

-- Flight 3: AA303 (JFK -> LHR, 150 seats: 30 First, 40 Business, 80 Economy)
INSERT INTO flights (
    id,
    flight_number,
    origin_airport,
    destination_airport,
    departure_time,
    arrival_time,
    origin_tz,
    status,
    total_capacity,
    schedule_version,
    created_at
) VALUES (
    '33333333-3333-3333-3333-333333333333'::UUID,
    'AA303',
    'JFK',
    'LHR',
    NOW() + INTERVAL '4 days' + INTERVAL '18 hours',
    NOW() + INTERVAL '5 days' + INTERVAL '6 hours',
    'America/New_York',
    'SCHEDULED',
    150,
    1,
    NOW()
);

-- ============================================================================
-- 2. INSERT FLIGHT CLASSES (Capacity, base fare, and overbooking limits)
-- ============================================================================

-- BA101 Classes (20F / 30B / 50E = 100)
INSERT INTO flight_classes (id, flight_id, seat_class, capacity, booked_seats, base_price_cents, overbooking_pct)
VALUES
    ('11111111-aaaa-1111-1111-111111111111'::UUID, '11111111-1111-1111-1111-111111111111'::UUID, 'FIRST', 20, 0, 250000, 0),
    ('11111111-bbbb-1111-1111-111111111111'::UUID, '11111111-1111-1111-1111-111111111111'::UUID, 'BUSINESS', 30, 0, 120000, 0),
    ('11111111-cccc-1111-1111-111111111111'::UUID, '11111111-1111-1111-1111-111111111111'::UUID, 'ECONOMY', 50, 0, 45000, 5);

-- PK202 Classes (10F / 20B / 50E = 80)
INSERT INTO flight_classes (id, flight_id, seat_class, capacity, booked_seats, base_price_cents, overbooking_pct)
VALUES
    ('22222222-aaaa-2222-2222-222222222222'::UUID, '22222222-2222-2222-2222-222222222222'::UUID, 'FIRST', 10, 0, 150000, 0),
    ('22222222-bbbb-2222-2222-222222222222'::UUID, '22222222-2222-2222-2222-222222222222'::UUID, 'BUSINESS', 20, 0, 80000, 0),
    ('22222222-cccc-2222-2222-222222222222'::UUID, '22222222-2222-2222-2222-222222222222'::UUID, 'ECONOMY', 50, 0, 30000, 5);

-- AA303 Classes (30F / 40B / 80E = 150)
INSERT INTO flight_classes (id, flight_id, seat_class, capacity, booked_seats, base_price_cents, overbooking_pct)
VALUES
    ('33333333-aaaa-3333-3333-333333333333'::UUID, '33333333-3333-3333-3333-333333333333'::UUID, 'FIRST', 30, 0, 350000, 0),
    ('33333333-bbbb-3333-3333-333333333333'::UUID, '33333333-3333-3333-3333-333333333333'::UUID, 'BUSINESS', 40, 0, 180000, 0),
    ('33333333-cccc-3333-3333-333333333333'::UUID, '33333333-3333-3333-3333-333333333333'::UUID, 'ECONOMY', 80, 0, 60000, 5);

-- ============================================================================
-- 3. INSERT PHYSICAL SEAT LAYOUTS (Exact 330 Seats)
-- ============================================================================

-- BA101 First Class: 20 seats (Rows 1-5, Seats A, B, C, D)
INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '11111111-1111-1111-1111-111111111111'::UUID,
    r || l,
    'FIRST'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM generate_series(1, 5) r
CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D']) l;

-- BA101 Business Class: 30 seats (Rows 10-14, Seats A, B, C, D, E, F)
INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '11111111-1111-1111-1111-111111111111'::UUID,
    r || l,
    'BUSINESS'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM generate_series(10, 14) r
CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D', 'E', 'F']) l;

-- BA101 Economy Class: 50 seats (Rows 20-27 A-F = 48 seats + Row 28 A-B = 2 seats)
INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '11111111-1111-1111-1111-111111111111'::UUID,
    r || l,
    'ECONOMY'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM generate_series(20, 27) r
CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D', 'E', 'F']) l;

INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '11111111-1111-1111-1111-111111111111'::UUID,
    28 || l,
    'ECONOMY'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM unnest(ARRAY['A', 'B']) l;

-- PK202 First Class: 10 seats (Rows 1-2 A-D = 8 seats + Row 3 A-B = 2 seats)
INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '22222222-2222-2222-2222-222222222222'::UUID,
    r || l,
    'FIRST'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM generate_series(1, 2) r
CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D']) l;

INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '22222222-2222-2222-2222-222222222222'::UUID,
    3 || l,
    'FIRST'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM unnest(ARRAY['A', 'B']) l;

-- PK202 Business Class: 20 seats (Rows 10-13, Seats A, B, C, D, E = 20 seats)
INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '22222222-2222-2222-2222-222222222222'::UUID,
    r || l,
    'BUSINESS'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM generate_series(10, 13) r
CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D', 'E']) l;

-- PK202 Economy Class: 50 seats (Rows 20-27 A-F = 48 seats + Row 28 A-B = 2 seats)
INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '22222222-2222-2222-2222-222222222222'::UUID,
    r || l,
    'ECONOMY'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM generate_series(20, 27) r
CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D', 'E', 'F']) l;

INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '22222222-2222-2222-2222-222222222222'::UUID,
    28 || l,
    'ECONOMY'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM unnest(ARRAY['A', 'B']) l;

-- AA303 First Class: 30 seats (Rows 1-5, Seats A, B, C, D, E, F = 30 seats)
INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '33333333-3333-3333-3333-333333333333'::UUID,
    r || l,
    'FIRST'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM generate_series(1, 5) r
CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D', 'E', 'F']) l;

-- AA303 Business Class: 40 seats (Rows 10-15 A-F = 36 seats + Row 16 A-D = 4 seats)
INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '33333333-3333-3333-3333-333333333333'::UUID,
    r || l,
    'BUSINESS'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM generate_series(10, 15) r
CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D', 'E', 'F']) l;

INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '33333333-3333-3333-3333-333333333333'::UUID,
    16 || l,
    'BUSINESS'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM unnest(ARRAY['A', 'B', 'C', 'D']) l;

-- AA303 Economy Class: 80 seats (Rows 20-32 A-F = 78 seats + Row 33 A-B = 2 seats)
INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '33333333-3333-3333-3333-333333333333'::UUID,
    r || l,
    'ECONOMY'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM generate_series(20, 32) r
CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D', 'E', 'F']) l;

INSERT INTO flight_seats (flight_id, seat_number, seat_class, status)
SELECT 
    '33333333-3333-3333-3333-333333333333'::UUID,
    33 || l,
    'ECONOMY'::seat_class_enum,
    'AVAILABLE'::seat_status_enum
FROM unnest(ARRAY['A', 'B']) l;

-- ============================================================================
-- 4. INSERT 5 CONFIRMED SAMPLE BOOKINGS & PASSENGERS
-- Across Basic Economy, Flexible, and Premium First
-- ============================================================================

-- Booking 1: BA101 - Seat 1A - PREMIUM_FIRST ($2,500.00 / 250000 cents)
INSERT INTO bookings (
    id,
    pnr,
    flight_id,
    seat_id,
    passenger_name,
    passenger_email,
    fare_class,
    fare_paid_cents,
    status,
    ip_address,
    created_at
)
SELECT 
    'a1111111-1111-1111-1111-111111111111'::UUID,
    'BAF001',
    '11111111-1111-1111-1111-111111111111'::UUID,
    s.id,
    'Alice Smith',
    'alice.smith@example.com',
    'PREMIUM_FIRST',
    250000,
    'CONFIRMED',
    '192.168.1.101',
    NOW() - INTERVAL '2 hours'
FROM flight_seats s
WHERE s.flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND s.seat_number = '1A';

-- Booking 2: BA101 - Seat 10A - FLEXIBLE ($1,200.00 / 120000 cents)
INSERT INTO bookings (
    id,
    pnr,
    flight_id,
    seat_id,
    passenger_name,
    passenger_email,
    fare_class,
    fare_paid_cents,
    status,
    ip_address,
    created_at
)
SELECT 
    'b2222222-2222-2222-2222-222222222222'::UUID,
    'BAB002',
    '11111111-1111-1111-1111-111111111111'::UUID,
    s.id,
    'Bob Jones',
    'bob.jones@example.com',
    'FLEXIBLE',
    120000,
    'CONFIRMED',
    '192.168.1.102',
    NOW() - INTERVAL '3 hours'
FROM flight_seats s
WHERE s.flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND s.seat_number = '10A';

-- Booking 3: BA101 - Seat 20A - BASIC_ECONOMY ($450.00 / 45000 cents)
INSERT INTO bookings (
    id,
    pnr,
    flight_id,
    seat_id,
    passenger_name,
    passenger_email,
    fare_class,
    fare_paid_cents,
    status,
    ip_address,
    created_at
)
SELECT 
    'c3333333-3333-3333-3333-333333333333'::UUID,
    'BAE003',
    '11111111-1111-1111-1111-111111111111'::UUID,
    s.id,
    'Charlie Brown',
    'charlie.brown@example.com',
    'BASIC_ECONOMY',
    45000,
    'CONFIRMED',
    '192.168.1.103',
    NOW() - INTERVAL '5 hours'
FROM flight_seats s
WHERE s.flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND s.seat_number = '20A';

-- Booking 4: PK202 - Seat 20A - BASIC_ECONOMY ($300.00 / 30000 cents)
INSERT INTO bookings (
    id,
    pnr,
    flight_id,
    seat_id,
    passenger_name,
    passenger_email,
    fare_class,
    fare_paid_cents,
    status,
    ip_address,
    created_at
)
SELECT 
    'd4444444-4444-4444-4444-444444444444'::UUID,
    'PKE004',
    '22222222-2222-2222-2222-222222222222'::UUID,
    s.id,
    'Diana Prince',
    'diana.prince@example.com',
    'BASIC_ECONOMY',
    30000,
    'CONFIRMED',
    '192.168.1.104',
    NOW() - INTERVAL '1 day'
FROM flight_seats s
WHERE s.flight_id = '22222222-2222-2222-2222-222222222222'::UUID AND s.seat_number = '20A';

-- Booking 5: AA303 - Seat 1A - PREMIUM_FIRST ($3,500.00 / 350000 cents)
INSERT INTO bookings (
    id,
    pnr,
    flight_id,
    seat_id,
    passenger_name,
    passenger_email,
    fare_class,
    fare_paid_cents,
    status,
    ip_address,
    created_at
)
SELECT 
    'e5555555-5555-5555-5555-555555555555'::UUID,
    'AAF005',
    '33333333-3333-3333-3333-333333333333'::UUID,
    s.id,
    'Evan Wright',
    'evan.wright@example.com',
    'PREMIUM_FIRST',
    350000,
    'CONFIRMED',
    '192.168.1.105',
    NOW() - INTERVAL '6 hours'
FROM flight_seats s
WHERE s.flight_id = '33333333-3333-3333-3333-333333333333'::UUID AND s.seat_number = '1A';

-- Update seat status and link booking_id for booked seats
UPDATE flight_seats 
SET status = 'BOOKED', booking_id = 'a1111111-1111-1111-1111-111111111111'::UUID
WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_number = '1A';

UPDATE flight_seats 
SET status = 'BOOKED', booking_id = 'b2222222-2222-2222-2222-222222222222'::UUID
WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_number = '10A';

UPDATE flight_seats 
SET status = 'BOOKED', booking_id = 'c3333333-3333-3333-3333-333333333333'::UUID
WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_number = '20A';

UPDATE flight_seats 
SET status = 'BOOKED', booking_id = 'd4444444-4444-4444-4444-444444444444'::UUID
WHERE flight_id = '22222222-2222-2222-2222-222222222222'::UUID AND seat_number = '20A';

UPDATE flight_seats 
SET status = 'BOOKED', booking_id = 'e5555555-5555-5555-5555-555555555555'::UUID
WHERE flight_id = '33333333-3333-3333-3333-333333333333'::UUID AND seat_number = '1A';

-- Register passenger manifest records
INSERT INTO passengers (booking_id, flight_id, seat_id, first_name, last_name, email, loyalty_tier)
VALUES
    ('a1111111-1111-1111-1111-111111111111'::UUID, '11111111-1111-1111-1111-111111111111'::UUID, (SELECT id FROM flight_seats WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_number = '1A'), 'Alice', 'Smith', 'alice.smith@example.com', 'PLATINUM'),
    ('b2222222-2222-2222-2222-222222222222'::UUID, '11111111-1111-1111-1111-111111111111'::UUID, (SELECT id FROM flight_seats WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_number = '10A'), 'Bob', 'Jones', 'bob.jones@example.com', 'GOLD'),
    ('c3333333-3333-3333-3333-333333333333'::UUID, '11111111-1111-1111-1111-111111111111'::UUID, (SELECT id FROM flight_seats WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_number = '20A'), 'Charlie', 'Brown', 'charlie.brown@example.com', 'GENERAL'),
    ('d4444444-4444-4444-4444-444444444444'::UUID, '22222222-2222-2222-2222-222222222222'::UUID, (SELECT id FROM flight_seats WHERE flight_id = '22222222-2222-2222-2222-222222222222'::UUID AND seat_number = '20A'), 'Diana', 'Prince', 'diana.prince@example.com', 'SILVER'),
    ('e5555555-5555-5555-5555-555555555555'::UUID, '33333333-3333-3333-3333-333333333333'::UUID, (SELECT id FROM flight_seats WHERE flight_id = '33333333-3333-3333-3333-333333333333'::UUID AND seat_number = '1A'), 'Evan', 'Wright', 'evan.wright@example.com', 'PLATINUM');

-- Synchronize booked_seats counters in flight_classes
UPDATE flight_classes 
SET booked_seats = 1 
WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_class = 'FIRST';

UPDATE flight_classes 
SET booked_seats = 1 
WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_class = 'BUSINESS';

UPDATE flight_classes 
SET booked_seats = 1 
WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_class = 'ECONOMY';

UPDATE flight_classes 
SET booked_seats = 1 
WHERE flight_id = '22222222-2222-2222-2222-222222222222'::UUID AND seat_class = 'ECONOMY';

UPDATE flight_classes 
SET booked_seats = 1 
WHERE flight_id = '33333333-3333-3333-3333-333333333333'::UUID AND seat_class = 'FIRST';

-- ============================================================================
-- 5. INSERT 2 WAITLIST ENTRIES (1 Platinum Tier, 1 General Tier)
-- ============================================================================

-- Waitlist 1: Platinum Tier on BA101 Business Class (Priority Score: 3190)
INSERT INTO waitlist (
    id,
    flight_id,
    passenger_name,
    passenger_email,
    loyalty_tier,
    requested_class,
    priority_score,
    status,
    created_at
) VALUES (
    '44444444-1111-1111-1111-111111111111'::UUID,
    '11111111-1111-1111-1111-111111111111'::UUID,
    'Sarah Connor',
    'sarah.connor@example.com',
    'PLATINUM',
    'BUSINESS',
    3190,
    'WAITING',
    NOW() - INTERVAL '1 hour'
);

-- Waitlist 2: General Tier on BA101 Business Class (Priority Score: 185)
INSERT INTO waitlist (
    id,
    flight_id,
    passenger_name,
    passenger_email,
    loyalty_tier,
    requested_class,
    priority_score,
    status,
    created_at
) VALUES (
    '44444444-2222-2222-2222-222222222222'::UUID,
    '11111111-1111-1111-1111-111111111111'::UUID,
    'John Doe',
    'john.doe@example.com',
    'GENERAL',
    'BUSINESS',
    185,
    'WAITING',
    NOW() - INTERVAL '2 hours'
);

-- ============================================================================
-- 6. INSERT 1 ACTIVE SEAT HOLD EXPIRING IN 2 MINUTES
-- Designed for immediate validation of sweep_expired_holds()
-- ============================================================================

INSERT INTO seat_holds (
    id,
    flight_id,
    seat_id,
    session_id,
    status,
    expires_at,
    created_at
)
SELECT 
    '55555555-1111-1111-1111-111111111111'::UUID,
    '11111111-1111-1111-1111-111111111111'::UUID,
    s.id,
    'demo-session-expiring-soon',
    'HELD',
    NOW() + INTERVAL '2 minutes',
    NOW()
FROM flight_seats s
WHERE s.flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND s.seat_number = '20B';

-- Mark seat 20B as HELD
UPDATE flight_seats
SET status = 'HELD'
WHERE flight_id = '11111111-1111-1111-1111-111111111111'::UUID AND seat_number = '20B';

COMMIT;
