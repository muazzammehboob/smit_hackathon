# ✈️ Flight Management System — Complete Understanding Guide

> **What this document is:** A plain-English, end-to-end explanation of your capstone project — what it is, why it exists, how every piece works together, and how to test it. Read this before touching any code.

---

## 1. The Big Picture — What Is This Project?

### In One Sentence
You're building a **mini airline backend** — the kind of software that runs behind the scenes when you book a flight on Emirates, PIA, or any airline website.

### What Problem Does It Solve?
When you go to an airline's website and book a seat, **hundreds of things** must happen correctly at the same time:
- Two people can't buy the same last seat simultaneously
- If you abandon your checkout, the seat must eventually become available again
- If a flight gets cancelled, every passenger must be notified and refunded
- Customer support needs to answer policy questions correctly based on YOUR specific ticket type
- The system must detect bots trying to mass-buy tickets
- Background jobs must run 24/7 to handle waitlists, reminders, and fraud

Your project solves ALL of these problems using a **dual-system architecture**.

### The Real-World Analogy
Think of it like a restaurant:

| Restaurant | Your System |
|:---|:---|
| **Waiter** (takes orders face-to-face, right now) | **FastAPI** (handles live user requests) |
| **Kitchen Manager** (prepares things in the background on a schedule — prep, cleaning, inventory checks) | **n8n** (runs scheduled background jobs) |
| **The Order Book** (single source of truth for what's been ordered) | **Supabase Postgres** (the database — the ONE place both systems read/write) |
| **Recipe Book + AI Assistant** | **Pinecone/pgvector + Gemini** (AI that answers customer policy questions) |

Both the waiter and the kitchen manager work with the same order book, but they do DIFFERENT jobs. The biggest challenge? **Making sure they don't step on each other's toes** (e.g., the waiter promises the last steak to a customer while the kitchen manager just gave it to someone else).

---

## 2. The Architecture — Two Writers, One Database

This is the **core concept** your professor is testing. It's called the **"Dual-Writer Problem"**.

```
┌─────────────────────┐     ┌─────────────────────────┐
│     FastAPI          │     │         n8n              │
│  (Live API Server)   │     │  (Background Automation) │
│                     │     │                          │
│ • User clicks "Book" │     │ • Every 2 min: check     │
│ • Admin creates flight│    │   waitlist promotions    │
│ • Search for flights │     │ • Every 15 min: scan for │
│ • Cancel booking     │     │   fraud                 │
│ • Join waitlist      │     │ • Every hour: send       │
│                     │     │   check-in reminders     │
│ Runs WHEN a user     │     │ Runs ON A SCHEDULE       │
│ makes a request      │     │ (no user involved)       │
└────────┬────────────┘     └────────┬────────────────┘
         │                           │
         │    BOTH write to the      │
         │    SAME database          │
         ▼                           ▼
┌────────────────────────────────────────────────┐
│           Supabase Postgres                     │
│         (Single Source of Truth)                 │
│                                                 │
│  The database has CHECK constraints, row locks, │
│  and enums that PREVENT corruption even if      │
│  both systems write at the same time.           │
└────────────────────────────────────────────────┘
```

### Why Is This Hard?

**Scenario:** There's 1 seat left on Flight BA123.

1. **FastAPI** receives a booking request from Customer A at 10:00:00.000
2. **n8n** (the waitlist promoter) decides to give that seat to Waitlisted Customer B at 10:00:00.001
3. **Both** try to assign the same seat at the same millisecond.

**Without proper locking:** Both succeed → the seat is double-booked → disaster.

**Your system's solution:** Postgres row-level locks (`SELECT ... FOR UPDATE SKIP LOCKED`) ensure only ONE system can touch a seat at a time. The other waits or skips.

> [!IMPORTANT]
> **This dual-writer concurrency problem is the #1 thing your professor is evaluating.** It's not about pretty UIs or fancy features — it's about whether your system can handle two independent writers without corrupting data.

---

## 3. The 10 Domains — What Each One Does

Your project is divided into 10 functional areas. Here's what each one means in plain English:

---

### Domain 1: Admin & Flight Management
**Who uses it:** Airline operations staff (admins)  
**What it does:** Create and manage flights  
**Real-world example:**

An airline admin logs in and says: *"We're starting a new London → Dubai route. Flight BA207 departs at 5 AM, has 100 seats: 20 First Class, 30 Business, 50 Economy."*

Your system:
1. Creates the flight record
2. Validates that 20 + 30 + 50 = 100 (must match total capacity exactly)
3. Generates a physical seat map (1A, 1B, ... 30F)
4. Logs WHO created it, WHEN, and WHAT the values were (audit trail)
5. Rejects if another BA207 already exists on the same day

**Key edge cases your professor cares about:**
- What if an admin tries to reduce Economy from 50 to 40, but 45 Economy seats are already booked? → **Rejected with HTTP 409** (can't shrink below booked count)
- What if they type the same origin and destination? → **Rejected** (can't fly London → London)

**Test it with:**
```bash
# Create a flight
curl -X POST http://localhost:8000/api/v1/admin/flights \
  -H "Content-Type: application/json" \
  -H "X-Admin-Role: SUPER_ADMIN" \
  -d '{
    "flight_number": "BA207",
    "origin_airport": "LHR",
    "destination_airport": "DXB",
    "departure_time": "2026-10-15T05:00:00Z",
    "arrival_time": "2026-10-15T12:00:00Z",
    "total_capacity": 100,
    "first_class_seats": 20,
    "business_class_seats": 30,
    "economy_class_seats": 50
  }'
# Expected: 201 Created

# Try creating same flight again → should fail
# Same curl command → Expected: 409 Conflict (duplicate flight)

# Try with wrong seat math (20 + 30 + 49 ≠ 100)
curl -X POST http://localhost:8000/api/v1/admin/flights \
  -H "Content-Type: application/json" \
  -H "X-Admin-Role: SUPER_ADMIN" \
  -d '{
    "flight_number": "BA208",
    "origin_airport": "LHR",
    "destination_airport": "DXB",
    "departure_time": "2026-10-16T05:00:00Z",
    "arrival_time": "2026-10-16T12:00:00Z",
    "total_capacity": 100,
    "first_class_seats": 20,
    "business_class_seats": 30,
    "economy_class_seats": 49
  }'
# Expected: 422 Unprocessable Entity (seats don't sum to total)

# Try without admin role
curl -X POST http://localhost:8000/api/v1/admin/flights \
  -H "Content-Type: application/json" \
  -d '{"flight_number": "BA209", ...}'
# Expected: 403 Forbidden
```

---

### Domain 2: Search & Fare Rules
**Who uses it:** Passengers searching for flights  
**What it does:** Shows available flights and locks prices for 15 minutes  
**Real-world example:**

A passenger searches: *"London to Dubai on October 15"*

Your system:
1. Queries all matching flights
2. Calculates REAL-TIME availability: `available = capacity - (booked + currently_held)`
3. Shows prices per class
4. Generates a **price lock token** (HMAC hash) valid for 15 minutes — so if the price changes while the user is entering payment, they still get the quoted price

**Three fare classes with different rules:**

| Fare Class | Price | Can Change? | Refundable? | Seat Choice? |
|:---|:---|:---|:---|:---|
| **Basic Economy** | Cheapest | ❌ No | ❌ \$0 refund | ❌ Random |
| **Flexible Economy** | Medium | ✅ Free up to 24h before | ⚠️ 90% refund (10% fee) | ✅ Yes |
| **Business / First** | Expensive | ✅ Always | ✅ 100% refund | ✅ Yes |

**Test it with:**
```bash
# Search for flights
curl "http://localhost:8000/api/v1/flights/search?origin=LHR&destination=DXB&date=2026-10-15"
# Expected: JSON with flights, available seats per class, prices, and a price_lock_token

# View seat map
curl "http://localhost:8000/api/v1/flights/{flight_id}/seats"
# Expected: List of physical seats with their status (AVAILABLE/HELD/BOOKED)
```

---

### Domain 3: Seat Holds & Booking (THE CORE)
**Who uses it:** Passengers booking flights  
**What it does:** Prevents two people from buying the same seat  
**Real-world example:**

You're on the Emirates website. You pick seat 14A. You go to enter your credit card. During those 10 minutes, that seat is **HELD** for you — nobody else can take it. If you don't pay within 10 minutes, it's released.

**The flow:**
```
1. POST /bookings/hold → Seat goes from AVAILABLE → HELD (10 min timer starts)
2. User enters payment details...
3. POST /bookings/confirm → Seat goes from HELD → BOOKED (PNR generated: "ABC123")
   OR
   10 minutes pass → Seat goes from HELD → AVAILABLE (hold expired)
```

**Why this is the hardest part:**
```
Scenario: Only 1 seat left. Two users click "Book" at the exact same millisecond.

WRONG approach (race condition):
  Thread A: READ seats → sees 1 available → WRITE booking ✓
  Thread B: READ seats → sees 1 available → WRITE booking ✓  ← OVERSOLD!

CORRECT approach (your system):
  Uses Postgres atomic update:
  UPDATE flight_classes SET booked_seats = booked_seats + 1
  WHERE flight_id = :id AND seat_class = :class
    AND (booked_seats + held_seats) < capacity
  RETURNING id;
  
  If 0 rows updated → seat is gone → HTTP 409 Conflict
```

**Idempotency protection:** If a user's network glitches and they send the same booking request twice, the `Idempotency-Key` header ensures the second request returns the cached first response (no double-charge).

**Test it with:**
```bash
# Step 1: Hold a seat
curl -X POST http://localhost:8000/api/v1/bookings/hold \
  -H "Idempotency-Key: test-hold-001" \
  -H "Content-Type: application/json" \
  -d '{
    "flight_id": "<FLIGHT_ID>",
    "seat_id": "<SEAT_ID>",
    "session_id": "user-session-abc"
  }'
# Expected: 201 with hold_id and held_until timestamp

# Step 2: Try to hold the SAME seat from another session (simulate another user)
curl -X POST http://localhost:8000/api/v1/bookings/hold \
  -H "Idempotency-Key: test-hold-002" \
  -H "Content-Type: application/json" \
  -d '{
    "flight_id": "<FLIGHT_ID>",
    "seat_id": "<SAME_SEAT_ID>",
    "session_id": "user-session-xyz"
  }'
# Expected: 409 Conflict (seat already held!)

# Step 3: Confirm the booking
curl -X POST http://localhost:8000/api/v1/bookings/confirm \
  -H "Idempotency-Key: test-confirm-001" \
  -H "Content-Type: application/json" \
  -d '{
    "hold_id": "<HOLD_ID from Step 1>",
    "passenger_name": "Ali Khan",
    "passenger_email": "ali@example.com",
    "fare_class": "FLEXIBLE",
    "fare_paid_cents": 45000
  }'
# Expected: 201 with PNR code like "ABC123" and booking details

# Step 4: Test idempotency — send the SAME confirm request again
curl -X POST http://localhost:8000/api/v1/bookings/confirm \
  -H "Idempotency-Key: test-confirm-001" \
  -H "Content-Type: application/json" \
  -d '{ ... same body ... }'
# Expected: 200 (cached response, NOT a new booking)

# Step 5: Look up booking by PNR
curl "http://localhost:8000/api/v1/bookings/<PNR>"
# Expected: Full booking details
```

---

### Domain 4: Changes, Cancellations & Refunds
**Who uses it:** Passengers cancelling, and the system auto-refunding  
**What it does:** Different refund amounts based on your ticket type  
**Real-world example:**

You bought a Basic Economy ticket for \$450 and want to cancel. Bad news: you get \$0 back.
Your colleague bought a Business Class ticket for \$2,000 and cancels. They get all \$2,000 back.

**Refund rules:**
```
BASIC_ECONOMY  → $0 refund     → Status: CANCELLED_NO_REFUND
FLEXIBLE       → 90% refund    → Status: REFUND_PENDING  
PREMIUM_FIRST  → 100% refund   → Status: REFUND_PENDING
```

**Partial cancellation:** A family of 4 books together. One person can't go. The system splits them off into a separate cancelled booking, refunds just their share, and keeps the other 3 confirmed.

**Airline disruptions:** If the airline changes your flight time by more than 3 hours, ALL ticket types get a full refund — even Basic Economy.

**The n8n role:** A daily background job checks if any refund has been stuck in "PENDING" for more than 3 days and sends an escalation email to the finance team.

**Test it with:**
```bash
# Cancel a Basic Economy booking
curl -X POST "http://localhost:8000/api/v1/bookings/<PNR>/cancel"
# Expected: {"refund_amount_cents": 0, "refund_type": "NONE", "status": "CANCELLED_NO_REFUND"}

# Cancel a Flexible booking (fare was 45000 cents = $450)
curl -X POST "http://localhost:8000/api/v1/bookings/<PNR>/cancel"
# Expected: {"refund_amount_cents": 40500, "refund_type": "CASH", "status": "REFUND_PENDING"}
# (45000 - 10% fee = 40500)

# Cancel a Business/First booking (fare was 200000 cents = $2,000)
curl -X POST "http://localhost:8000/api/v1/bookings/<PNR>/cancel"
# Expected: {"refund_amount_cents": 200000, "refund_type": "CASH", "status": "REFUND_PENDING"}
```

---

### Domain 5: Waitlist & Standby
**Who uses it:** Passengers who want a sold-out flight  
**What it does:** Automatically gives freed-up seats to waiting passengers  
**Real-world example:**

Flight BA207 Economy is sold out. You really want on that flight. You join the waitlist. The system calculates your priority:

$$\text{Priority} = (\text{Loyalty Tier} \times 1000) + (\text{Fare Class} \times 100) - \text{Hours Since Epoch}$$

- A Platinum member joining at hour 500,000 gets: `(3×1000) + (1×100) - 500000 = -496900`
- A General member joining at hour 500,001 gets: `(0×1000) + (1×100) - 500001 = -499901`
- **Platinum member wins** (higher score = higher priority)

**The flow:**
```
1. Passenger joins waitlist (FastAPI)
2. Someone cancels their booking → seat frees up
3. Every 2 minutes, n8n checks: "Any flights with free seats AND waitlisted people?"
4. n8n grabs the TOP priority waitlisted person (using FOR UPDATE SKIP LOCKED)
5. n8n gives them a 2-hour HOLD and sends them an email: "Claim your seat!"
6. If they don't claim within 2 hours → seat goes to the next person
```

**The concurrency danger:** A gate agent manually assigns a standby seat via FastAPI at the EXACT same time n8n's background worker tries to promote someone. Both systems lock the row first to prevent double-assignment.

**Test it with:**
```bash
# Join the waitlist
curl -X POST "http://localhost:8000/api/v1/flights/<FLIGHT_ID>/waitlist" \
  -H "Content-Type: application/json" \
  -d '{
    "passenger_name": "Sara Ahmed",
    "passenger_email": "sara@example.com",
    "loyalty_tier": "PLATINUM",
    "requested_class": "ECONOMY"
  }'
# Expected: 201 with priority_score and position

# Check position
curl "http://localhost:8000/api/v1/flights/<FLIGHT_ID>/waitlist/position?email=sara@example.com"
# Expected: Current position number

# Now cancel a booking on the same flight to free a seat, 
# then wait 2 minutes for n8n to auto-promote
# Check the waitlist table in Supabase → Sara's status should change to PROMOTED
```

---

### Domain 6: Scheduled Automations (n8n Only)
**Who uses it:** Nobody directly — these run automatically  
**What they do:** Background jobs that keep the system healthy

| Workflow | Schedule | What It Does |
|:---|:---|:---|
| **Hold Sweeper** | Every 60 seconds | Finds expired seat holds (past 10-min TTL) and releases seats back to AVAILABLE |
| **Waitlist Promoter** | Every 2 minutes | Checks if any sold-out flights now have seats, promotes top waitlisted person |
| **Check-in Reminder** | Every hour | Sends "Check in now!" emails to passengers departing in 24-25 hours (timezone-correct) |
| **Fraud Scanner** | Every 15 minutes | Flags IPs that booked 5+ tickets in 10 minutes as SUSPECTED_FRAUD |
| **Refund Escalation** | Daily at midnight | Alerts finance if any refund has been PENDING for 3+ days |
| **Ops Report** | Daily at 23:59 UTC | Calculates load factors and revenue per flight, emails digest to management |
| **Reconciliation** | Daily at 03:00 UTC | Cross-checks that booked_seats counter matches actual booked seats — alerts on discrepancy |

**The key insight:** These are NOT called by any user. They run on a clock inside n8n Cloud. They read/write the SAME Postgres database that FastAPI uses.

**Test it by:**
1. Go to your n8n Cloud dashboard
2. Look for workflows named like "01_TTL_Seat_Hold_Sweeper"
3. Check they're activated (green toggle)
4. For the hold sweeper: manually insert an expired hold in Supabase (`expires_at` in the past), wait 60 seconds, check if the seat flipped back to AVAILABLE
5. For fraud: create 5 bookings from the same IP in Supabase with `created_at` within the last 10 minutes, wait 15 minutes, check if they're flagged `SUSPECTED_FRAUD`

---

### Domain 7: Fraud, Policy & RAG Support (AI Layer)
**Who uses it:** Customers asking policy questions, and the system detecting fraud  
**What it does:** Uses AI to draft answers AND catches bot attacks

#### RAG (Retrieval-Augmented Generation) — The AI Support System

**The problem:** A customer emails: *"Can I get a refund if I cancel?"*  
**The naive AI answer:** *"Yes, most tickets are refundable."* ← **WRONG** if they have a Basic Economy ticket.  
**Your system's answer:** Looks up the customer's ACTUAL fare class, retrieves the SPECIFIC policy for that fare class from the vector database, and drafts an accurate answer.

**The flow:**
```
1. Customer submits: "Can I get a refund?" (with their PNR booking code)
2. System looks up their booking → fare_class = BASIC_ECONOMY
3. System embeds the question into a 768-dimensional vector
4. System searches policy_embeddings table for relevant policy chunks
   filtered by fare_class = BASIC_ECONOMY
5. Gemini AI drafts: "Based on your Basic Economy ticket, refunds are 
   not permitted. You may receive travel credit with a $50 fee."
6. ⚠️ CRITICAL: Draft is NEVER sent directly to customer!
7. Draft is saved to support_draft_approvals (status: PENDING)
8. An email goes to the Ops Agent with [Approve] and [Reject] buttons
9. ONLY after human approval → email is sent to the customer
```

> [!CAUTION]
> **The Human Approval Gate is MANDATORY.** This is a core requirement. AI-drafted responses must NEVER be sent directly to customers without human review. This is about liability — an AI hallucination could promise a refund the airline can't honor.

#### Fraud Detection

**The problem:** A bot creates hundreds of bookings to scalp seats.  
**Your system's detection:** Every 15 minutes, n8n scans for IPs that created 5+ bookings in the last 10 minutes. If found, all those bookings are flagged as `SUSPECTED_FRAUD`.

**Test RAG with:**
```bash
# Submit a policy question
curl -X POST "http://localhost:8000/api/v1/support/inquire" \
  -H "Content-Type: application/json" \
  -d '{
    "pnr": "<A_VALID_PNR>",
    "question": "Can I get a refund if I cancel my ticket?"
  }'
# Expected: {"status": "draft_created", "draft_id": "..."}

# Check Supabase → support_draft_approvals table → should have a PENDING row
# with the AI-drafted response

# Approve the draft
curl -X POST "http://localhost:8000/api/v1/support/approve" \
  -H "Content-Type: application/json" \
  -d '{"draft_id": "<DRAFT_ID>", "action": "approve"}'
# Expected: Draft status changes to APPROVED, email sent to customer
```

**Test Fraud Detection with:**
```bash
# Create 5 rapid bookings from the same IP (via Supabase SQL Editor)
INSERT INTO bookings (pnr, flight_id, passenger_name, passenger_email, fare_class, 
                      fare_paid_cents, status, ip_address, created_at)
VALUES 
  ('FR001', '<flight_id>', 'Bot 1', 'bot@evil.com', 'BASIC_ECONOMY', 10000, 'CONFIRMED', '192.168.1.100', NOW()),
  ('FR002', '<flight_id>', 'Bot 2', 'bot@evil.com', 'BASIC_ECONOMY', 10000, 'CONFIRMED', '192.168.1.100', NOW()),
  ('FR003', '<flight_id>', 'Bot 3', 'bot@evil.com', 'BASIC_ECONOMY', 10000, 'CONFIRMED', '192.168.1.100', NOW()),
  ('FR004', '<flight_id>', 'Bot 4', 'bot@evil.com', 'BASIC_ECONOMY', 10000, 'CONFIRMED', '192.168.1.100', NOW()),
  ('FR005', '<flight_id>', 'Bot 5', 'bot@evil.com', 'BASIC_ECONOMY', 10000, 'CONFIRMED', '192.168.1.100', NOW());

-- Wait 15 minutes for the fraud scanner n8n workflow to run
-- Then check:
SELECT pnr, status FROM bookings WHERE ip_address = '192.168.1.100';
-- Expected: All 5 should show status = 'SUSPECTED_FRAUD'
```

---

### Domain 8: Approval & Autonomy Boundaries
**What this is:** A design POLICY, not code  
**Why it matters:** Some actions are too risky for automation — humans must approve them

| Action | Auto or Human? | Why? |
|:---|:---|:---|
| Sending a check-in reminder | ✅ Auto | Low risk — worst case, a harmless extra email |
| Expiring a seat hold | ✅ Auto | Deterministic, no judgment needed |
| Standard refund (in-policy) | ✅ Auto | Clear rules, no ambiguity |
| AI-drafted customer email | ❌ **Human Required** | AI could hallucinate, causing legal liability |
| Schedule-change compensation | ❌ **Human Required** | Financial impact, case-by-case judgment |
| Fraud cancellation | ❌ **Human Required** | Could wrongly cancel a legitimate booking |

---

### Domain 9: Shared Database Architecture
**What this is:** The "contract" between FastAPI and n8n  
**Why it matters:** Since BOTH systems write to the same database independently, there must be strict rules about WHO can write WHAT

**Table ownership rules:**

| Table | FastAPI | n8n |
|:---|:---|:---|
| `flights`, `flight_classes` | ✅ WRITE | 👁️ READ ONLY |
| `bookings`, `passengers` | ✅ WRITE | 👁️ READ (except fraud flag) |
| `seat_holds` | ✅ CREATE/CONFIRM | ✅ EXPIRE old holds |
| `waitlist` | ✅ CREATE (join) | ✅ UPDATE (promote/expire) |
| `notification_logs` | ❌ | ✅ WRITE |
| `daily_ops_reports` | ❌ | ✅ WRITE |
| `admin_audit_logs` | ✅ APPEND | ✅ APPEND |

**Database-level safety nets (even if code has bugs):**
```sql
-- Seats can never go negative
CHECK (booked_seats >= 0)

-- Seats can never exceed capacity
CHECK (booked_seats <= capacity)

-- Flight capacity must add up
CHECK (first_class + business + economy = total_capacity)

-- Can't fly to the same place
CHECK (origin_airport != destination_airport)

-- Must arrive after departing
CHECK (arrival_time > departure_time)
```

These `CHECK` constraints run INSIDE Postgres. Even if your code has a bug, the database itself will reject invalid data.

**Test it by:**
```sql
-- Try to break the constraint directly in Supabase SQL Editor:
UPDATE flight_classes SET booked_seats = -1 WHERE id = '<some_id>';
-- Expected: ERROR — violates CHECK constraint

UPDATE flight_classes SET booked_seats = 999 WHERE id = '<some_id>';
-- Expected: ERROR — violates CHECK constraint (exceeds capacity)
```

---

### Domain 10: Core Infrastructure
**What this is:** The technical foundation everything else relies on

- **Pydantic V2 Schemas:** Every API request and response has a strict type contract. If you send `"seats": "ten"` instead of `"seats": 10`, it's rejected with HTTP 422.
- **Idempotency Keys:** Every mutating endpoint requires an `Idempotency-Key` header (UUID). Prevents double-charges from network retries.
- **Supabase Postgres:** The single ledger of truth. All prices stored in integer cents (no floating-point rounding errors).
- **Gmail Integration:** FastAPI sends transactional emails (booking confirmations). n8n sends scheduled emails (reminders, alerts).

---

## 4. Complete User Journey — Start to Finish

Here's what a complete user journey looks like, touching ALL 10 domains:

```
Day 1: Admin creates a flight
  ├── Domain 1: POST /admin/flights (LHR→DXB, 100 seats)
  └── Domain 10: Audit log records creation with admin ID

Day 2: Passenger searches for flights
  ├── Domain 2: GET /flights/search?origin=LHR&destination=DXB
  └── Gets price_lock_token valid for 15 minutes

Day 2: Passenger books a seat
  ├── Domain 3: POST /bookings/hold (seat 14A held for 10 min)
  ├── Domain 3: POST /bookings/confirm (seat booked, PNR issued)
  └── Domain 10: Confirmation email sent via Gmail

Day 2: Flight is now sold out → another passenger joins waitlist
  └── Domain 5: POST /flights/{id}/waitlist (priority calculated)

Day 2: First passenger cancels their Flexible ticket
  ├── Domain 4: POST /bookings/{pnr}/cancel (90% refund calculated)
  ├── Domain 4: Seat released back to AVAILABLE
  └── Domain 9: booked_seats counter decremented

2 minutes later: n8n auto-promotes the waitlisted passenger
  ├── Domain 5/6: Waitlist promoter finds freed seat
  ├── Domain 5: PROMOTED status, 2-hour claim window
  └── Domain 6: Gmail notification sent

Day 3: Customer asks "Can I change my flight?"
  ├── Domain 7: RAG looks up their fare class + policy
  ├── Domain 7: Gemini drafts response
  ├── Domain 8: Draft saved as PENDING (NOT sent directly)
  └── Domain 8: Ops agent reviews → approves → email dispatched

Day 3: n8n detects suspicious IP with 5+ bookings
  ├── Domain 7: Fraud scanner flags bookings
  └── Domain 8: Alert sent to fraud team for human review

Nightly: Reconciliation job runs
  ├── Domain 6: Sweeps orphaned holds
  ├── Domain 9: Cross-checks booked_seats vs actual physical seats
  └── Domain 10: Alerts ops if any discrepancy found
```

---

## 5. What Your Professor Is REALLY Testing

Your professor isn't testing whether you can make a REST API. They're testing your understanding of these **7 architectural concepts**:

### 1. Atomicity & Race Conditions
> "Can two users book the last seat simultaneously without overselling?"

**Proof:** `SELECT ... FOR UPDATE` row locks, atomic `UPDATE ... WHERE (booked + held) < capacity RETURNING id`

### 2. Dual-Writer Consistency
> "When FastAPI and n8n both modify the same tables, does the data stay consistent?"

**Proof:** Table ownership rules, database-level CHECK constraints, `SKIP LOCKED` strategy

### 3. Idempotency
> "If a network retry causes a duplicate request, does the system handle it gracefully?"

**Proof:** `Idempotency-Key` header checked against `idempotency_records` table

### 4. Temporal Safety (TTL & Holds)
> "Does a held seat eventually get released if payment never comes?"

**Proof:** 10-minute hold expiry, n8n sweeper cleaning up every 60 seconds

### 5. Business Rule Branching
> "Do different fare classes get different refund amounts?"

**Proof:** `BASIC_ECONOMY → $0`, `FLEXIBLE → 90%`, `PREMIUM_FIRST → 100%`

### 6. AI Grounding & Human-in-the-Loop
> "Does the AI answer based on the customer's ACTUAL ticket, not generic knowledge?"

**Proof:** Vector search filtered by `fare_class`, mandatory human approval gate

### 7. Audit & Accountability
> "Can you trace every change to who made it and when?"

**Proof:** `admin_audit_logs` with `previous_state` and `new_state` JSONB diffs

---

## 6. Complete API Endpoint Reference

### Admin Endpoints (Requires `X-Admin-Role: SUPER_ADMIN` header)

| Method | Endpoint | Purpose |
|:---|:---|:---|
| `POST` | `/api/v1/admin/flights` | Create a new flight |
| `GET` | `/api/v1/admin/flights` | List all flights |
| `GET` | `/api/v1/admin/flights/{id}` | Get flight details with seat map |
| `POST` | `/api/v1/admin/flights/{id}/seat-map` | Define physical seat layout |
| `PATCH` | `/api/v1/admin/flights/{id}/capacity` | Adjust class seat allocation |
| `PATCH` | `/api/v1/admin/flights/{id}/schedule` | Change departure/arrival times |
| `POST` | `/api/v1/admin/flights/{id}/cancel` | Cancel entire flight |
| `GET` | `/api/v1/admin/audit-logs` | View admin action history |

### Passenger Endpoints

| Method | Endpoint | Purpose |
|:---|:---|:---|
| `GET` | `/api/v1/flights/search` | Search flights by route/date |
| `GET` | `/api/v1/flights/{id}/seats` | View seat map with statuses |
| `POST` | `/api/v1/bookings/hold` | Hold a seat (10 min timer) |
| `POST` | `/api/v1/bookings/confirm` | Confirm booking (pay) |
| `GET` | `/api/v1/bookings/{pnr}` | Look up booking by PNR |
| `POST` | `/api/v1/bookings/{pnr}/cancel` | Cancel booking (full) |
| `POST` | `/api/v1/bookings/{pnr}/cancel-passenger` | Cancel one person from group |
| `POST` | `/api/v1/bookings/{pnr}/travel-credit` | Get travel credit voucher |

### Waitlist Endpoints

| Method | Endpoint | Purpose |
|:---|:---|:---|
| `POST` | `/api/v1/flights/{id}/waitlist` | Join waitlist |
| `GET` | `/api/v1/flights/{id}/waitlist/position` | Check your position |
| `DELETE` | `/api/v1/flights/{id}/waitlist/{id}` | Remove from waitlist |
| `POST` | `/api/v1/waitlist/{id}/claim` | Claim a promoted seat |

### Support & Webhooks

| Method | Endpoint | Purpose |
|:---|:---|:---|
| `POST` | `/api/v1/support/inquire` | Submit policy question (triggers RAG) |
| `POST` | `/api/v1/support/approve` | Approve/reject AI draft |
| `POST` | `/api/v1/webhooks/trigger-hold-sweep` | Manually trigger hold cleanup |
| `POST` | `/api/v1/webhooks/trigger-waitlist-promotion` | Manually trigger waitlist check |
| `POST` | `/api/v1/webhooks/trigger-fraud-scan` | Manually trigger fraud scan |
| `GET` | `/health` | Health check |

---

## 7. Database Tables Quick Reference

| Table | Owner | Purpose |
|:---|:---|:---|
| `flights` | FastAPI | Flight definitions (route, times, capacity) |
| `flight_classes` | FastAPI | Per-class capacity and booking counts |
| `flight_seats` | FastAPI | Physical seat map (1A, 12C, etc.) with status |
| `seat_holds` | FastAPI + n8n | Temporary seat locks with TTL |
| `bookings` | FastAPI | Confirmed bookings with PNR, fare, status |
| `waitlist` | FastAPI + n8n | Waitlisted passengers with priority scores |
| `refunds` | FastAPI | Refund records per cancellation |
| `travel_credits` | FastAPI | Voucher credits with expiry dates |
| `idempotency_records` | FastAPI | Duplicate request detection |
| `admin_audit_logs` | Both | Immutable action history |
| `notification_logs` | n8n | Email send tracking |
| `daily_ops_reports` | n8n | Daily revenue & load factor data |
| `support_draft_approvals` | n8n | AI-drafted responses awaiting human review |
| `ops_escalations` | n8n | Fraud alerts and stuck refund escalations |
| `policy_embeddings` | n8n | Vector embeddings of policy documents |
| `price_alert_subscriptions` | FastAPI | User price-drop alert preferences |

---

## 8. The 10-Step Demo Test Script

Run these 10 steps in order to prove your entire system works:

### Step 1: Create a Flight ✈️
```bash
curl -X POST http://localhost:8000/api/v1/admin/flights \
  -H "Content-Type: application/json" \
  -H "X-Admin-Role: SUPER_ADMIN" \
  -d '{
    "flight_number": "PK301",
    "origin_airport": "KHI",
    "destination_airport": "DXB",
    "departure_time": "2026-10-20T06:00:00Z",
    "arrival_time": "2026-10-20T08:30:00Z",
    "total_capacity": 10,
    "first_class_seats": 2,
    "business_class_seats": 3,
    "economy_class_seats": 5
  }'
```
✅ **Expected:** 201 Created with flight ID. Save the `id` for next steps.

### Step 2: Search for the Flight 🔍
```bash
curl "http://localhost:8000/api/v1/flights/search?origin=KHI&destination=DXB&date=2026-10-20"
```
✅ **Expected:** Flight PK301 appears with 2 First, 3 Business, 5 Economy available seats and a `price_lock_token`.

### Step 3: Hold a Seat 🔒
```bash
curl -X POST http://localhost:8000/api/v1/bookings/hold \
  -H "Idempotency-Key: demo-hold-001" \
  -H "Content-Type: application/json" \
  -d '{"flight_id": "<FLIGHT_ID>", "seat_id": "<AN_ECONOMY_SEAT_ID>", "session_id": "demo-session"}'
```
✅ **Expected:** 201 with `hold_id` and `held_until` (10 minutes from now).

### Step 4: Anti-Overselling Test ⚔️
```bash
# Try the SAME seat from a different session
curl -X POST http://localhost:8000/api/v1/bookings/hold \
  -H "Idempotency-Key: demo-hold-002" \
  -H "Content-Type: application/json" \
  -d '{"flight_id": "<FLIGHT_ID>", "seat_id": "<SAME_SEAT_ID>", "session_id": "attacker-session"}'
```
✅ **Expected:** 409 Conflict. The atomic lock prevented double-booking.

### Step 5: Confirm Booking ✅
```bash
curl -X POST http://localhost:8000/api/v1/bookings/confirm \
  -H "Idempotency-Key: demo-confirm-001" \
  -H "Content-Type: application/json" \
  -d '{
    "hold_id": "<HOLD_ID>",
    "passenger_name": "Ahmed Raza",
    "passenger_email": "ahmed@example.com",
    "fare_class": "FLEXIBLE",
    "fare_paid_cents": 35000
  }'
```
✅ **Expected:** 201 with a PNR (like "PK3A7B"). Save this PNR.

### Step 6: Idempotency Test 🔁
```bash
# Send exact same request with same key
curl -X POST http://localhost:8000/api/v1/bookings/confirm \
  -H "Idempotency-Key: demo-confirm-001" \
  -H "Content-Type: application/json" \
  -d '{ ... same body ... }'
```
✅ **Expected:** 200 (cached response). No duplicate booking created.

### Step 7: Cancel & Test Refund Branching 💸
```bash
curl -X POST "http://localhost:8000/api/v1/bookings/<PNR>/cancel"
```
✅ **Expected:** Flexible fare → 90% refund = 31,500 cents (\$315 back from \$350).

### Step 8: Waitlist Join + Auto-Promotion 📋
```bash
# First, book ALL remaining Economy seats to make it full
# Then join waitlist:
curl -X POST "http://localhost:8000/api/v1/flights/<FLIGHT_ID>/waitlist" \
  -H "Content-Type: application/json" \
  -d '{
    "passenger_name": "Waitlisted Person",
    "passenger_email": "waiting@example.com",
    "loyalty_tier": "GOLD",
    "requested_class": "ECONOMY"
  }'
```
✅ **Expected:** 201 with priority score. Then cancel one Economy booking → wait 2 minutes → check if waitlisted person got PROMOTED in the database.

### Step 9: RAG Policy Question 🤖
```bash
curl -X POST "http://localhost:8000/api/v1/support/inquire" \
  -H "Content-Type: application/json" \
  -d '{"pnr": "<A_VALID_PNR>", "question": "What is the baggage allowance for my ticket?"}'
```
✅ **Expected:** Draft created → check `support_draft_approvals` table → PENDING status → AI drafted a response based on the customer's actual fare class.

### Step 10: View Audit Trail 📜
```bash
curl "http://localhost:8000/api/v1/admin/audit-logs" \
  -H "X-Admin-Role: SUPER_ADMIN"
```
✅ **Expected:** Complete history of every admin action with timestamps, admin IDs, and before/after JSON diffs.

---

## 9. Key Technology Stack

| Technology | Role | Why This? |
|:---|:---|:---|
| **FastAPI** | API server | Fast, async, auto-docs, Pydantic validation |
| **Supabase Postgres** | Database | Managed Postgres with REST API, RLS, pgvector |
| **n8n Cloud** | Background automation | Visual workflow builder, schedule triggers, Gmail integration |
| **Pydantic V2** | Request/response validation | Type safety, field constraints, auto-serialization |
| **pgvector** | Vector search | Stores policy document embeddings for RAG |
| **Google Gemini** | AI model | Generates grounded policy responses |
| **Gmail API** | Email | Sends booking confirmations, reminders, alerts |
| **Render** | Hosting | Deploys FastAPI to the cloud (free tier) |

---

## 10. Common Mistakes to Avoid

> [!WARNING]
> These are the things that will lose you marks:

1. **NOT using row locks for seat booking** — If you use a simple read-check-write pattern, two users can book the same seat. Always use `SELECT ... FOR UPDATE` or atomic `UPDATE ... RETURNING`.

2. **Sending AI responses directly to customers** — The human approval gate is MANDATORY. Draft → Human Review → Only Then Send.

3. **Ignoring the dual-writer problem** — If n8n and FastAPI can both modify the same row without locking, you'll get data corruption. Use `SKIP LOCKED`.

4. **Storing prices as floats** — `$4.50 + $3.20 = $7.699999999997` with floats. Store as integer cents: `450 + 320 = 770`.

5. **Not implementing idempotency** — Network retries will double-charge customers without idempotency keys.

6. **Hardcoding timezone as UTC everywhere** — Check-in reminders must respect the departure airport's local timezone, not UTC.

7. **No audit logging** — Every admin action must be logged with who, what, when, before-state, and after-state.

---

> [!TIP]
> **How to use this document:** Read it fully first to understand the PURPOSE. Then use the test commands in Section 8 to verify each piece works. If something fails, check the specific domain section for expected behavior and edge cases.
