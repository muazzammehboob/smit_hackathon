# 🚀 Flight Management System - 10-Minute Master Demo Script

This script provides exactly what you need to impress the judges: live concurrency, auto-cascading logic, background workflows, and the RAG-powered support agent.

---

### Step 1: Admin Flight Creation
*LHR->DXB with 100 seats, instant layout generation.*

```bash
curl -X POST "http://localhost:8000/api/v1/admin/flights" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer ADMIN_TOKEN" \
     -d '{
           "flight_number": "EK-001",
           "departure_airport": "LHR",
           "arrival_airport": "DXB",
           "departure_time": "2026-10-01T10:00:00Z",
           "arrival_time": "2026-10-01T20:00:00Z",
           "total_seats": 100,
           "base_price": 500
         }'
```

---

### Step 2: Live Flight Search
*Shows real-time available seats and 15-min HMAC price lock token.*

```bash
curl -X GET "http://localhost:8000/api/v1/search/flights?origin=LHR&destination=DXB&date=2026-10-01" \
     -H "Accept: application/json"
```
*Note the `price_lock_token` from the response for the next steps.*

---

### Step 3: Atomic Seat Hold
*Locks seat row in DB, sets hold TTL.*

```bash
curl -X POST "http://localhost:8000/api/v1/bookings" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: HOLD-KEY-001" \
     -d '{
           "flight_id": "<FLIGHT_ID_FROM_STEP_1>",
           "passenger_name": "Alice Hold",
           "passenger_email": "alice@example.com",
           "seat_id": "<SEAT_ID>",
           "fare_class": "FLEXIBLE",
           "price_lock_token": "<TOKEN_FROM_STEP_2>"
         }'
```

---

### Step 4: Concurrency & Overselling Prevention Test
*Dual simultaneous curl commands on last seat: one returns 201, other returns 409 Conflict.*

**Terminal 1:**
```bash
curl -X POST "http://localhost:8000/api/v1/bookings" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: TERM-1-KEY-001" \
     -d '{
           "flight_id": "<FLIGHT_ID_FROM_STEP_1>",
           "passenger_name": "Alice Concurrent",
           "passenger_email": "alice@example.com",
           "seat_id": "<SEAT_ID>",
           "fare_class": "FLEXIBLE",
           "price_lock_token": "<TOKEN_FROM_STEP_2>"
         }'
```

**Terminal 2:**
```bash
curl -X POST "http://localhost:8000/api/v1/bookings" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: TERM-2-KEY-002" \
     -d '{
           "flight_id": "<FLIGHT_ID_FROM_STEP_1>",
           "passenger_name": "Bob Concurrent",
           "passenger_email": "bob@example.com",
           "seat_id": "<SEAT_ID>",
           "fare_class": "FLEXIBLE",
           "price_lock_token": "<TOKEN_FROM_STEP_2>"
         }'
```

---

### Step 5: Idempotency Protection
*Resend same booking request with same Idempotency-Key: returns cached confirmation with 0 duplicate charge.*

```bash
curl -X POST "http://localhost:8000/api/v1/bookings" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: TERM-1-KEY-001" \
     -d '{
           "flight_id": "<FLIGHT_ID_FROM_STEP_1>",
           "passenger_name": "Alice Concurrent",
           "passenger_email": "alice@example.com",
           "seat_id": "<SEAT_ID>",
           "fare_class": "FLEXIBLE",
           "price_lock_token": "<TOKEN_FROM_STEP_2>"
         }'
```

---

### Step 6: Fare-Branching Cancellations
*Cancel Basic Economy -> $0 refund; Cancel Flexible -> 90% refund.*

**Cancel Basic Economy (Expect $0 refund):**
```bash
curl -X POST "http://localhost:8000/api/v1/bookings/<BASIC_PNR>/cancel" \
     -H "Content-Type: application/json"
```

**Cancel Flexible (Expect 90% refund):**
```bash
curl -X POST "http://localhost:8000/api/v1/bookings/<ALICE_PNR>/cancel" \
     -H "Content-Type: application/json"
```

---

### Step 7: Waitlist Auto-Promotion
*Freed seat triggers promotion via SKIP LOCKED, sends alert.*

```bash
# Add a user to the waitlist
curl -X POST "http://localhost:8000/api/v1/waitlist" \
     -H "Content-Type: application/json" \
     -d '{
           "flight_id": "<FLIGHT_ID_FROM_STEP_1>",
           "passenger_name": "Charlie Waitlist",
           "passenger_email": "charlie@example.com",
           "priority_tier": "GOLD"
         }'

# Trigger n8n background workflow endpoint
curl -X POST "http://localhost:8000/api/v1/webhooks/trigger-waitlist-promotion"
```

---

### Step 8: Bot & Burst Fraud Detection
*Simulate 5 quick bookings from 1 IP -> flags SUSPECTED_FRAUD.*

```bash
# Trigger the fraud scanner workflow webhook
curl -X POST "http://localhost:8000/api/v1/webhooks/trigger-fraud-scan"
```

---

### Step 9: Grounded RAG Support with Human Gate
*Customer asks policy question -> AI retrieves chunks -> Drafts response -> Admin clicks Approve -> Email dispatched.*

```bash
# 1. Submit Inquiry
curl -X POST "http://localhost:8000/api/v1/support/inquire" \
     -H "Content-Type: application/json" \
     -d '{
           "pnr": "<ALICE_PNR>",
           "question": "Can I get a refund on my ticket?"
         }'

# 2. Ops Agent Approves
curl -X POST "http://localhost:8000/api/v1/support/approve" \
     -H "Content-Type: application/json" \
     -d '{
           "id": "<DRAFT_ID>",
           "action": "approve"
         }'
```

---

### Step 10: Immutable Audit Log
*Shows audit trail entries with JSON diffs and admin ID.*

```bash
curl -X GET "http://localhost:8000/api/v1/admin/audit-logs" \
     -H "Authorization: Bearer ADMIN_TOKEN"
```
