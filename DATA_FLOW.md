# Data Flow Architecture & Technical Specifications
## Dual-Writer Flight Management System (FastAPI + n8n + Supabase Postgres)

---

## 1. System Architecture Overview

```mermaid
flowchart TB
    subgraph Clients ["Clients & External Users"]
        Customer["Customer / Passenger (Web / Mobile)"]
        AdminUser["Airline Ops / Gate Agent"]
        EmailUser["Passenger / CSR (Gmail)"]
    end

    subgraph API_Layer ["Synchronous Write Layer (FastAPI on Render)"]
        Router["FastAPI Routers"]
        IdempMiddleware["Idempotency Verification"]
        PydanticModels["Pydantic V2 Validation"]
        TxEngine["Atomic Transaction Engine"]
    end

    subgraph DB_Layer ["Single Ledger of Truth (Supabase Postgres)"]
        FlightsTbl[("flights")]
        FlightClassesTbl[("flight_classes")]
        SeatsTbl[("flight_seats")]
        HoldsTbl[("seat_holds")]
        BookingsTbl[("bookings")]
        WaitlistTbl[("waitlist")]
        AuditTbl[("admin_audit_logs")]
        RAGTbl[("support_draft_approvals")]
        VectorStore[("policy_embeddings (pgvector / Pinecone)")]
    end

    subgraph Automation_Layer ["Asynchronous Batch & AI Layer (n8n Cloud)"]
        HoldSweeper["TTL Seat Hold Sweeper (Every 60s)"]
        WaitlistPromoter["Waitlist Promotion Worker (Every 2m)"]
        ReminderCron["Timezone Check-in Sender (Hourly)"]
        FraudScanner["Fraud Scoring Scanner (Every 15m)"]
        RAGWorkflow["RAG Policy Pipeline + Gmail Node"]
        ReconciliationCron["Nightly Inventory Reconciler (Daily)"]
    end

    Customer -->|Search / Hold / Book / Cancel| Router
    AdminUser -->|Create Flight / Reschedule / Gate Standby| Router
    Router --> IdempMiddleware --> PydanticModels --> TxEngine
    TxEngine -->|ACID SQL Writes & Row Locks| DB_Layer

    Automation_Layer -->|Direct SQL Queries & SKIP LOCKED Updates| DB_Layer
    RAGWorkflow -->|Vector Search & Booking Context| VectorStore
    RAGWorkflow -->|Draft Approval Email| AdminUser
    AdminUser -->|Approve Draft Click| RAGWorkflow
    RAGWorkflow -->|Send Final Email| EmailUser
```

---

## 2. End-to-End Data Flows

### Flow 1: Flight Creation & Aircraft Capacity Invariants (FastAPI)
```mermaid
sequenceDiagram
    autonumber
    actor Admin as Super Admin
    participant API as FastAPI (/admin/flights)
    participant Val as Pydantic V2 Validator
    participant DB as Supabase Postgres

    Admin->>API: POST /flights (Capacity, Classes, Datetimes)
    API->>Val: Validate positive seats, origin != destination, arrival > departure
    alt Validation Failure
        Val-->>Admin: HTTP 422 Unprocessable Entity
    else Valid Payload
        API->>DB: Check duplicate: flight_no + origin + date
        alt Duplicate Found
            DB-->>Admin: HTTP 409 Conflict (Flight already exists on date)
        else Unique Flight
            API->>DB: BEGIN TRANSACTION
            Note over API,DB: DB Invariant: first + business + economy == total_capacity
            API->>DB: INSERT INTO flights (...)
            API->>DB: INSERT INTO flight_classes (first, business, economy)
            API->>DB: INSERT INTO flight_seats (generate physical layout 1A..30F)
            API->>DB: INSERT INTO admin_audit_logs (...)
            API->>DB: COMMIT TRANSACTION
            DB-->>API: Success (flight_id, 201 Created)
            API-->>Admin: Return Flight Details & Generated Seat Map
        end
    end
```

---

### Flow 2: Live Search & Price Lock (FastAPI)
```mermaid
sequenceDiagram
    autonumber
    actor User as Passenger
    participant API as FastAPI (/flights/search)
    participant DB as Supabase Postgres

    User->>API: GET /flights/search?origin=LHR&destination=DXB&date=2026-10-01
    API->>DB: SELECT f.*, fc.seat_class, fc.capacity, fc.booked_seats,<br/>(SELECT COUNT(*) FROM seat_holds WHERE flight_id=f.id AND status='HELD' AND expires_at > NOW()) as active_holds<br/>FROM flights f JOIN flight_classes fc ON f.id=fc.flight_id WHERE ...
    DB-->>API: Available Seat Counts per Class
    API->>API: Calculate Available = Capacity - (Booked + Active Holds)
    API->>API: Generate HMAC Price Lock Hash (valid for 15 minutes)
    API-->>User: Return Routes, Remaining Seats, Fare Options, and price_lock_token
```

---

### Flow 3: Atomic Seat Hold (TTL) & Idempotent Booking (FastAPI)
```mermaid
sequenceDiagram
    autonumber
    actor User as Passenger
    participant API as FastAPI (/bookings/checkout)
    participant DB as Supabase Postgres
    participant Ext as Stripe / Payment Gateway

    User->>API: POST /bookings/hold (flight_id, seat_number, price_lock_token)<br/>Header: Idempotency-Key: abc-123
    API->>DB: BEGIN TRANSACTION
    API->>DB: SELECT * FROM flight_seats WHERE flight_id=:id AND seat_number=:num FOR UPDATE
    alt Seat already HELD or BOOKED
        API->>DB: ROLLBACK
        API-->>User: HTTP 409 Conflict (Seat no longer available)
    else Seat AVAILABLE
        API->>DB: INSERT INTO seat_holds (flight_id, seat_id, expires_at = NOW() + INTERVAL '10 MIN')
        API->>DB: UPDATE flight_seats SET status = 'HELD' WHERE id = :seat_id
        API->>DB: COMMIT TRANSACTION
        API-->>User: HTTP 201 (hold_id, held_until)
    end

    Note over User,API: Passenger Enters Payment Details within 10 Minutes
    User->>API: POST /bookings/confirm (hold_id, payment_token)<br/>Header: Idempotency-Key: def-456
    API->>DB: Check Idempotency Record (def-456)
    alt Already processed
        API-->>User: Return Cached Booking Confirmation (HTTP 200)
    else First Time Request
        API->>DB: BEGIN TRANSACTION
        API->>DB: SELECT * FROM seat_holds WHERE id = :hold_id AND expires_at > NOW() FOR UPDATE
        API->>Ext: Charge Payment Card
        API->>DB: INSERT INTO bookings (pnr, passenger_id, flight_id, fare_paid, status='CONFIRMED')
        API->>DB: UPDATE flight_seats SET status = 'BOOKED' WHERE id = :seat_id
        API->>DB: UPDATE flight_classes SET booked_seats = booked_seats + 1 WHERE ...
        API->>DB: UPDATE seat_holds SET status = 'CONVERTED' WHERE id = :hold_id
        API->>DB: INSERT INTO idempotency_records (key, response_payload)
        API->>DB: COMMIT TRANSACTION
        API-->>User: Return Confirmed PNR & Booking Receipt
    end
```

---

### Flow 4: Passenger Cancellation & Partial Group Splitting (FastAPI)
```mermaid
sequenceDiagram
    autonumber
    actor User as Passenger
    participant API as FastAPI (/bookings/{id}/cancel)
    participant DB as Supabase Postgres

    User->>API: POST /bookings/B123/cancel (passenger_ids: ["P2"])
    API->>DB: BEGIN TRANSACTION
    API->>DB: SELECT b.*, f.departure_time FROM bookings b JOIN flights f ON b.flight_id = f.id WHERE b.id = 'B123' FOR UPDATE
    
    Note over API: Multi-Passenger Partial Cancellation Logic
    alt Full Booking Cancellation (Single passenger or all passengers)
        API->>API: Evaluate Fare Rules:
        Note over API: Basic Economy -> $0 Refund<br/>Flexible -> Refund minus fee<br/>Business -> 100% Refund
        API->>DB: UPDATE bookings SET status = 'CANCELLED'
        API->>DB: UPDATE flight_seats SET status = 'AVAILABLE' WHERE booking_id = 'B123'
        API->>DB: UPDATE flight_classes SET booked_seats = booked_seats - count
        API->>DB: INSERT INTO refunds (booking_id, amount_cents, status)
    else Partial Cancellation (1 passenger out of 3)
        API->>DB: INSERT INTO bookings (parent_pnr, passenger_id='P2', status='CANCELLED')
        API->>DB: REMOVE 'P2' from parent booking passenger list
        API->>DB: UPDATE flight_seats SET status = 'AVAILABLE' WHERE passenger_id = 'P2'
        API->>DB: UPDATE flight_classes SET booked_seats = booked_seats - 1
        API->>DB: INSERT INTO refunds (proportional_amount_cents, status)
    end
    API->>DB: COMMIT TRANSACTION
    API-->>User: Cancellation Confirmed & Proportional Refund Breakdown
```

---

### Flow 5: Waitlist Auto-Promotion with Concurrency Lock (n8n + Postgres)
```mermaid
sequenceDiagram
    autonumber
    participant n8n as n8n Waitlist Worker (Cron 2m)
    participant DB as Supabase Postgres
    participant GateAgent as Gate Agent (FastAPI)
    participant Gmail as Gmail Node / Passenger

    n8n->>DB: BEGIN TRANSACTION
    n8n->>DB: SELECT flight_id FROM flight_classes WHERE (capacity - booked_seats) > 0
    Note over n8n,DB: Prevent Race with Gate Agent: FOR UPDATE SKIP LOCKED
    n8n->>DB: SELECT * FROM waitlist<br/>WHERE flight_id = :id AND status = 'WAITING'<br/>ORDER BY priority_score DESC LIMIT 1<br/>FOR UPDATE SKIP LOCKED
    
    critical Concurrent Gate Action
        GateAgent->>DB: Standby Reassignment Attempt
        Note over GateAgent,DB: Gate agent blocked or skips locked row until transaction finishes
    end

    n8n->>DB: INSERT INTO seat_holds (flight_id, passenger_id, expires_at = NOW() + INTERVAL '2h')
    n8n->>DB: UPDATE waitlist SET status = 'PROMOTED', claim_deadline = NOW() + INTERVAL '2h'<br/>WHERE id = :waitlist_id
    n8n->>DB: COMMIT TRANSACTION
    n8n->>Gmail: Send "Seat Freed! You have 2 hours to claim your ticket"
    Gmail-->>n8n: Sent
```

---

### Flow 6: Timezone-Aware Check-in Reminder with Disruption Suppression (n8n)
```mermaid
sequenceDiagram
    autonumber
    participant n8n as n8n Hourly Cron
    participant DB as Supabase Postgres
    participant Gmail as Gmail Node

    n8n->>DB: Run Timezone-Offset Query:
    Note over n8n,DB: SELECT b.id, b.passenger_email, f.flight_number, f.origin_tz<br/>FROM bookings b JOIN flights f ON b.flight_id = f.id<br/>WHERE f.status != 'CANCELLED' AND f.status != 'DELAYED'<br/>AND f.departure_time AT TIME ZONE f.origin_tz<br/>BETWEEN (NOW() AT TIME ZONE f.origin_tz + INTERVAL '24h')<br/>    AND (NOW() AT TIME ZONE f.origin_tz + INTERVAL '25h')<br/>AND NOT EXISTS (SELECT 1 FROM notification_logs WHERE booking_id=b.id AND type='CHECKIN_REMINDER')
    
    DB-->>n8n: Eligible Passenger List (Suppresses Cancelled Flights)
    loop For Each Passenger (Batched)
        n8n->>Gmail: Send Timezone-Correct Boarding Reminder
        n8n->>DB: INSERT INTO notification_logs (booking_id, type='CHECKIN_REMINDER', status='SENT')
    end
```

---

### Flow 7: Context-Grounded RAG Policy Inquiry with Human Approval Gate
```mermaid
sequenceDiagram
    autonumber
    actor Cust as Customer
    participant n8n as n8n RAG Workflow
    participant DB as Supabase Postgres
    participant Vector as Supabase pgvector / Pinecone
    participant LLM as OpenAI / Gemini
    actor Agent as Ops Agent (CSR)
    participant Gmail as Passenger Gmail

    Cust->>n8n: Inbound Policy Question: "Can I get a refund if I cancel?" (with PNR)
    n8n->>DB: SELECT b.fare_class, b.fare_paid, f.departure_time FROM bookings b ... WHERE pnr = :pnr
    DB-->>n8n: Customer Fare Class = 'BASIC_ECONOMY'
    n8n->>Vector: Vector Similarity Query (Query Embedding, Filter: {fare_class: 'BASIC_ECONOMY'})
    Vector-->>n8n: Relevant Clause: "Basic Economy fares are non-refundable"
    n8n->>LLM: Generate Draft Response with Strict Grounding
    LLM-->>n8n: Draft: "Based on your Basic Economy ticket, refunds are not permitted..."
    
    Note over n8n,Agent: MANDATORY HUMAN APPROVAL GATE
    n8n->>DB: INSERT INTO support_draft_approvals (pnr, draft_text, status='PENDING')
    n8n->>Agent: Send Review Alert via Email/Slack with [Approve] and [Edit] links
    
    alt Agent Rejects / Edits
        Agent->>n8n: Submit Modified Response
        n8n->>DB: UPDATE support_draft_approvals SET status='EDITED_APPROVED'
    else Agent Approves
        Agent->>n8n: Click [Approve]
        n8n->>DB: UPDATE support_draft_approvals SET status='APPROVED'
    end
    
    n8n->>Gmail: Dispatch Approved Email to Customer
    Gmail-->>Cust: Customer Receives Verified, Zero-Hallucination Policy Reply
```

---

### Flow 8: Bot & Mass-Booking Fraud Scoring Worker (n8n)
```mermaid
sequenceDiagram
    autonumber
    participant n8n as n8n Fraud Scanner (Every 15m)
    participant DB as Supabase Postgres
    participant Alert as Security Webhook

    n8n->>DB: SELECT ip_address, card_fingerprint, COUNT(*) as booking_count<br/>FROM bookings WHERE created_at > NOW() - INTERVAL '10 MIN'<br/>GROUP BY ip_address, card_fingerprint HAVING COUNT(*) >= 5
    DB-->>n8n: Suspect Clusters
    loop For each suspect booking
        n8n->>DB: UPDATE bookings SET status = 'SUSPECTED_FRAUD'<br/>WHERE ip_address = :ip AND created_at > NOW() - INTERVAL '10 MIN'
        n8n->>DB: INSERT INTO ops_escalations (reason='BOT_BURST_ATTACK', severity='HIGH')
        n8n->>Alert: Send High-Priority Alert to Fraud Ops
    end
```

---

### Flow 9: Nightly Inventory Reconciliation & Orphan Hold Reclaimer (n8n)
```mermaid
sequenceDiagram
    autonumber
    participant n8n as n8n Reconciler (Daily 03:00 UTC)
    participant DB as Supabase Postgres
    participant Ops as Ops Team Alert

    Note over n8n,DB: Step 1: Reclaim Orphaned Holds
    n8n->>DB: UPDATE flight_seats SET status = 'AVAILABLE'<br/>WHERE id IN (SELECT seat_id FROM seat_holds WHERE status = 'HELD' AND expires_at < NOW())
    n8n->>DB: UPDATE seat_holds SET status = 'EXPIRED' WHERE status = 'HELD' AND expires_at < NOW()

    Note over n8n,DB: Step 2: Invariant Balance Check
    n8n->>DB: SELECT f.id as flight_id, fc.seat_class, fc.booked_seats as recorded_booked,<br/>COUNT(s.id) FILTER (WHERE s.status = 'BOOKED') as actual_booked<br/>FROM flights f JOIN flight_classes fc ON f.id = fc.flight_id<br/>LEFT JOIN flight_seats s ON f.id = s.flight_id AND fc.seat_class = s.seat_class<br/>GROUP BY f.id, fc.seat_class, fc.booked_seats<br/>HAVING fc.booked_seats != COUNT(s.id) FILTER (WHERE s.status = 'BOOKED')
    
    alt Variance Found (> 0 mismatch)
        DB-->>n8n: Mismatch Detected (Ghost Booking / Corrupted Counter)
        n8n->>Ops: DISPATCH SEVERITY-1 INVENTORY DISCREPANCY ALERT
        n8n->>DB: INSERT INTO ops_audit_logs (action='RECONCILIATION_FAIL', details=...)
    else Zero Variance
        DB-->>n8n: All Flights 100% Invariant Compliant
        n8n->>DB: INSERT INTO ops_audit_logs (action='RECONCILIATION_PASS')
    end
```

---

## 3. Database Schema & Invariants (Supabase Postgres DDL)

To guarantee that n8n direct writes can **never** corrupt FastAPI state, the schema enforces constraints at the database engine level:

```sql
-- Custom Enums
CREATE TYPE flight_status_enum AS ENUM ('SCHEDULED', 'BOARDING', 'DEPARTED', 'ARRIVED', 'CANCELLED', 'DELAYED');
CREATE TYPE seat_class_enum AS ENUM ('FIRST', 'BUSINESS', 'ECONOMY');
CREATE TYPE seat_status_enum AS ENUM ('AVAILABLE', 'HELD', 'BOOKED', 'BLOCKED');
CREATE TYPE booking_status_enum AS ENUM ('HELD', 'CONFIRMED', 'CANCELLED', 'REFUNDED', 'SUSPECTED_FRAUD');
CREATE TYPE fare_class_enum AS ENUM ('BASIC_ECONOMY', 'FLEXIBLE', 'PREMIUM_FIRST');
CREATE TYPE waitlist_status_enum AS ENUM ('WAITING', 'PROMOTED', 'EXPIRED_UNCLAIMED', 'CONVERTED', 'CANCELLED');

-- 1. Flights Table
CREATE TABLE flights (
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

-- 2. Flight Classes (Capacity Breakdown & Live Inventory)
CREATE TABLE flight_classes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    seat_class seat_class_enum NOT NULL,
    capacity INT NOT NULL,
    booked_seats INT NOT NULL DEFAULT 0,
    base_price_cents INT NOT NULL,
    overbooking_pct INT NOT NULL DEFAULT 0,
    
    CONSTRAINT check_class_capacity_positive CHECK (capacity >= 0),
    CONSTRAINT check_booked_seats_positive CHECK (booked_seats >= 0),
    CONSTRAINT check_inventory_limit CHECK (booked_seats <= capacity * (1 + overbooking_pct / 100.0)),
    CONSTRAINT unique_flight_class UNIQUE (flight_id, seat_class)
);

-- 3. Physical Flight Seats Map
CREATE TABLE flight_seats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    seat_number VARCHAR(5) NOT NULL,
    seat_class seat_class_enum NOT NULL,
    status seat_status_enum NOT NULL DEFAULT 'AVAILABLE',
    booking_id UUID,
    
    CONSTRAINT unique_seat_per_flight UNIQUE (flight_id, seat_number)
);

-- 4. Temporary Seat Holds (Anti-Oversell TTL)
CREATE TABLE seat_holds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    seat_id UUID NOT NULL REFERENCES flight_seats(id) ON DELETE CASCADE,
    session_id VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'HELD',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_seat_holds_active ON seat_holds (flight_id, status, expires_at);

-- 5. Bookings Ledger
CREATE TABLE bookings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pnr VARCHAR(10) NOT NULL UNIQUE,
    parent_pnr VARCHAR(10),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE RESTRICT,
    passenger_name VARCHAR(100) NOT NULL,
    passenger_email VARCHAR(150) NOT NULL,
    fare_class fare_class_enum NOT NULL,
    fare_paid_cents INT NOT NULL,
    status booking_status_enum NOT NULL DEFAULT 'CONFIRMED',
    ip_address VARCHAR(45) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_bookings_lookup ON bookings (pnr, passenger_email);
CREATE INDEX idx_bookings_fraud_scan ON bookings (ip_address, created_at);

-- 6. Waitlist Table
CREATE TABLE waitlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    passenger_name VARCHAR(100) NOT NULL,
    passenger_email VARCHAR(150) NOT NULL,
    loyalty_tier VARCHAR(20) NOT NULL DEFAULT 'GENERAL',
    requested_class seat_class_enum NOT NULL,
    priority_score BIGINT NOT NULL,
    status waitlist_status_enum NOT NULL DEFAULT 'WAITING',
    claim_deadline TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_waitlist_priority ON waitlist (flight_id, requested_class, status, priority_score DESC);

-- 7. Idempotency Records
CREATE TABLE idempotency_records (
    key VARCHAR(100) PRIMARY KEY,
    endpoint VARCHAR(100) NOT NULL,
    response_code INT NOT NULL,
    response_body JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 8. Support Draft Approvals (Human-in-the-Loop RAG Gate)
CREATE TABLE support_draft_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pnr VARCHAR(10) NOT NULL,
    customer_email VARCHAR(150) NOT NULL,
    inquiry_text TEXT NOT NULL,
    retrieved_policy_snippet TEXT NOT NULL,
    draft_response TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    reviewer_id VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```
