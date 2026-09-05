# Product Requirements Document (PRD)
## Project: Resilient Dual-Writer Flight Management System (FMS)
**Architecture:** FastAPI (Synchronous Live Path) + n8n Cloud (Asynchronous/Batch Automation) + Supabase Postgres + Pinecone / pgvector + Gmail  
**Deployment Target:** Render (FastAPI) + Vercel (Frontend) + Supabase (Database) + n8n Cloud  
**Hackathon Constraints:** 12-Hour Build Timeline, Free-Tier Cloud Only, NO Docker, NO Local n8n.

---

## 1. Executive Summary & Core Philosophy

The Flight Management System (FMS) is a high-reliability airline inventory, booking, and operations platform designed to solve the classic **Dual-Writer Distributed System Problem**:
1. **FastAPI** handles all live, user-facing, low-latency, ACID-transactional operations (flight definitions, seat search, atomic seat holds, bookings, cancellations).
2. **n8n Cloud** acts as an autonomous background agent executing scheduled cron workflows, event-driven data processing, waitlist promotions, fraud scans, and AI customer-service workflows directly against Postgres.
3. **Supabase Postgres** serves as the **Single Source of Truth** and the **Supreme Invariant Gatekeeper**. Because n8n bypasses FastAPI validation and code contracts, Postgres-level constraints (`CHECK`, foreign keys, custom ENUMs, triggers, and row locks) guarantee that neither system can ever corrupt inventory or create split-brain states.

---

## 2. System Stakeholders & User Personas

| Persona | Primary Interface | Key Needs & Permissions |
| :--- | :--- | :--- |
| **Customer / Passenger** | Web Client (Vercel) / API | Real-time seat availability search, temporary seat holds during checkout, atomic booking, self-service cancellation, policy queries. |
| **Waitlisted Passenger** | Email (Gmail) / Web | Automated promotion notifications when seats free up, claim time-window validation. |
| **Operations Agent (Gate / CSR)** | Admin Dashboard | Flight schedule adjustments, manual seat reassignments, standby handling, RAG email approval gate. |
| **Super Admin** | Admin Dashboard / API | Flight creation, physical seat map layout allocation, flight cancellation, system audit log inspection. |
| **Autonomous Agent (n8n)** | Direct Postgres SQL / Webhook | Polling worker for waitlist promotions, TTL hold cleanup, timezone-aware reminder dispatches, fraud pattern scoring. |

---

## 3. The 10 Functional Domains & Complete Feature Requirements

### Domain 1: Admin & Flight Management
*Owner: `[FastAPI]` exclusive*

* **FR-1.1: Flight Creation:**
  - Endpoint `POST /api/v1/admin/flights` to define new routes with `flight_number`, `origin_airport` (IATA 3-letter code), `destination_airport` (IATA 3-letter code), `departure_time` (timestamptz), and `arrival_time` (timestamptz).
  - *Edge Case Validation:* Origin and destination cannot be identical (`origin != destination`). Arrival datetime must be strictly after departure datetime (`arrival_time > departure_time`).
* **FR-1.2: Aircraft Capacity & Class Allocation:**
  - Admin specifies seat breakdown across three strict classes: `first_class_capacity`, `business_class_capacity`, and `economy_class_capacity`.
  - *Invariant Gate:* Database `CHECK (first_class_capacity + business_class_capacity + economy_class_capacity = total_capacity)`.
  - *Edge Case Validation:* Seat counts must be strictly positive integers ($\ge 0$ for class allocations, $> 0$ for total aircraft capacity). Negative or non-integer values rejected with HTTP 422.
* **FR-1.3: Duplicate Flight Protection:**
  - Reject duplicate flight numbers operating on the same route on the same UTC calendar day.
  - *Enforcement:* Unique constraint `UNIQUE(flight_number, origin_airport, DATE(departure_time))`.
* **FR-1.4: Seat Map & Physical Layout Configuration:**
  - Endpoint `POST /api/v1/admin/flights/{id}/seat-map` mapping physical seat coordinates (e.g., `1A`, `12C`, `34F`) to corresponding fare classes.
  - Invariant: Physical seat count per class must match the declared aircraft class capacity exactly.
* **FR-1.5: Downward Capacity Reallocation Guard:**
  - When an admin attempts to shrink a class capacity (e.g., reducing Economy from 50 to 40):
  - *Edge Case Rule:* System queries `COUNT(*) WHERE flight_id = :id AND class = 'ECONOMY' AND status IN ('BOOKED', 'HELD')`. If `new_capacity < currently_reserved`, the request is rejected with HTTP 409 Conflict.
* **FR-1.6: Flight Schedule Changes & Cascading Propagation:**
  - Admin endpoint `PATCH /api/v1/admin/flights/{id}/schedule` to adjust departure/arrival times.
  - *Cascading Rule:* Updates flight record, sets `schedule_version = schedule_version + 1`, logs audit record, flags all linked active bookings with `flight_schedule_disrupted = TRUE`, and queues passenger notifications.
* **FR-1.7: Master Flight Cancellation:**
  - Admin endpoint `POST /api/v1/admin/flights/{id}/cancel` marking flight status as `CANCELLED`.
  - *Cascading Rule:* Releases all temporary seat holds, cancels all waitlist entries, marks all confirmed bookings as `ELIGIBLE_FOR_REBOOKING_OR_REFUND`, and writes an event to `ops_audit_log`.
* **FR-1.8: Tiered Role-Based Access Control (RBAC):**
  - Two tiers: `SUPER_ADMIN` (can create flights, cancel flights, modify capacity) vs. `OPS_AGENT` (can view flights, reassign gate seats, approve RAG drafts).
* **FR-1.9: Immutable Administrative Audit Log:**
  - Every administrative action inserts a row into `admin_audit_logs` capturing `admin_user_id`, `action_type`, `resource_id`, `previous_state` (JSONB), `new_state` (JSONB), `ip_address`, and `created_at`.

---

### Domain 2: Search & Fare Rules
*Owner: `[FastAPI]` exclusive*

* **FR-2.1: Live Inventory Search:**
  - Endpoint `GET /api/v1/flights/search` filtered by origin, destination, departure date, and optional seat class.
  - Returns real-time available seat inventory: `available_seats = total_class_capacity - (booked_seats + active_held_seats)`.
* **FR-2.2: Fare Class Hierarchy & Policies:**
  - **Basic Economy:** Lowest price point. Zero free changes, non-refundable (cancellation yields \$0 refund or travel credit with penalty), random seat assignment only.
  - **Flexible Economy:** Medium price point. Free changes up to 24h prior, refundable to original payment method minus standard processing fee.
  - **Business / First Class:** Highest price point. 100% fully refundable anytime before departure, complimentary seat selection, priority waitlist weighting.
* **FR-2.3: Price-Hold Guarantees:**
  - When search results are returned, a price quote hash is generated valid for 15 minutes (`price_lock_expires_at`). If checkout initiates within 15 minutes, the fare is guaranteed regardless of dynamic pricing fluctuations.
* **FR-2.4: Multi-Leg / Connecting Itinerary Safety:**
  - When searching/booking multi-leg journeys (e.g., KHI $\rightarrow$ DXB $\rightarrow$ LHR):
  - *Atomic Leg Invariant:* Both leg holds must succeed together in a single transactional unit. If Leg 1 holds a seat but Leg 2 fails due to sudden sell-out, Leg 1 hold is immediately aborted and rolled back (no stranded one-way holds).
* **FR-2.5: Currency & Locale Normalization:**
  - All fares are stored in the database in integer cents USD (e.g., `$450.00` = `45000`). Displayed responses format currency with ISO-4217 code and locale formatting.

---

### Domain 3: Seat Holds & Booking (The Atomic Core)
*Owner: `[FastAPI]` exclusive*

* **FR-3.1: Temporary Seat Hold with Expiry (TTL):**
  - Endpoint `POST /api/v1/bookings/hold` creates a reservation lock on selected seat(s) with `held_until = NOW() + INTERVAL '10 minutes'`.
  - Seat status transitions to `HELD`. If payment is not completed within 10 minutes, the hold expires and becomes available for re-allocation.
* **FR-3.2: Atomic Seat Decrement (Anti-Overselling Guard):**
  - System avoids naive `read -> check -> write` race conditions.
  - Executes atomic update in Postgres:
    ```sql
    UPDATE flight_classes 
    SET booked_seats = booked_seats + 1 
    WHERE flight_id = :flight_id AND seat_class = :class 
      AND (booked_seats + held_seats) < capacity
    RETURNING id;
    ```
  - If 0 rows updated, returns immediate HTTP 409 "Seat Sold Out".
* **FR-3.3: Strict Idempotency Key Handling:**
  - All booking creation endpoints require the `Idempotency-Key` header (UUIDv4).
  - Key is checked against `idempotency_records` table. Duplicate submissions within 24 hours return the original cached response with zero re-processing.
* **FR-3.4: Configurable Overbooking Buffers:**
  - Overbooking configuration per flight class:
    - `FIRST`: Strict 0% buffer (never oversell).
    - `BUSINESS`: Strict 0% buffer.
    - `ECONOMY`: Up to 5% buffer configurable by Super Admin for historical no-show routes.
* **FR-3.5: Group Booking All-or-Nothing Rule:**
  - Group booking request for $N$ seats ($1 \le N \le 9$):
  - *Rule:* Atomic all-or-nothing guarantee. If $N=4$ requested but only 3 seats remain, the entire booking is rejected (no partial split reservations).
* **FR-3.6: Booking Cutoff Windows:**
  - First / Business class allows booking up to 60 minutes prior to scheduled departure.
  - Economy class closes booking strictly 180 minutes (3 hours) prior to scheduled departure.

---

### Domain 4: Changes, Cancellations & Refunds
*Owner: `[FastAPI]` for synchronous calculation & execution; `[n8n]` for scheduled escalation*

* **FR-4.1: Fare-Type Cancellation Branching `[FastAPI]`:**
  - When a user requests cancellation via `POST /api/v1/bookings/{id}/cancel`:
    - **Basic Economy:** Refund amount = \$0. Status updated to `CANCELLED_NO_REFUND`.
    - **Flexible Economy:** Calculates partial refund = `fare_paid - cancellation_fee`. Status = `REFUND_PENDING`.
    - **Business / First:** Refund amount = `fare_paid` (100%). Status = `REFUND_PENDING`.
  - *Inventory Release:* Seat status is immediately set back to `AVAILABLE`, decrementing booked count.
* **FR-4.2: Partial Cancellation on Multi-Passenger Bookings `[FastAPI]`:**
  - In a booking with multiple passengers, when one passenger cancels:
  - System splits the passenger into a separate child booking record marked `CANCELLED`, computes proportional refund for that seat, releases that physical seat, and leaves remaining passengers confirmed under the parent PNR.
* **FR-4.3: Airline-Initiated Disruptions `[FastAPI]`:**
  - If a flight schedule changes by $> 180$ minutes or is cancelled by the airline:
  - Fare policy is overridden automatically: all fare classes (including Basic Economy) become eligible for 100% cash refund or complimentary rebooking.
* **FR-4.4: Travel Credit Voucher Management `[FastAPI]`:**
  - If passenger elects travel credit instead of credit card refund:
  - Generates a unique voucher in `travel_credits` with `balance_cents` and `expires_at = now() + INTERVAL '365 days'`.
* **FR-4.5: Unresolved Refund Escalation `[n8n]`:**
  - Scheduled n8n cron workflow runs daily.
  - Queries `SELECT * FROM refunds WHERE status = 'PENDING' AND created_at < NOW() - INTERVAL '3 days'`.
  - Triggers an escalation email to finance ops and logs an incident.

---

### Domain 5: Waitlist & Standby Orchestration
*Owner: `[FastAPI]` for joining; `[n8n]` for promotion; `[Both]` for concurrency locking*

* **FR-5.1: Join Waitlist `[FastAPI]`:**
  - Endpoint `POST /api/v1/flights/{id}/waitlist` allows passengers to join when a class is full.
* **FR-5.2: Waitlist Priority Algorithm `[FastAPI]`:**
  - Deterministic priority formula:
    $$\text{Priority Score} = (\text{Loyalty Tier Weight} \times 1000) + (\text{Fare Class Weight} \times 100) - (\text{Waitlist Entry Timestamp (Epoch Hours)})$$
  - *Loyalty Weights:* Platinum = 3, Gold = 2, Silver = 1, General = 0.
  - *Class Weights:* First = 3, Business = 2, Economy = 1.
* **FR-5.3: Automated Waitlist Promotion Worker `[n8n]`:**
  - Scheduled n8n workflow runs every 2 minutes.
  - Identifies flights where `available_seats > 0` and active waitlist entries exist.
  - Claims the top-ranked waitlisted passenger atomically using `FOR UPDATE SKIP LOCKED`.
  - Transitions waitlist status from `WAITING` $\rightarrow$ `PROMOTED`, reserves a seat hold, and records `claim_deadline = NOW() + INTERVAL '2 hours'`.
* **FR-5.4: Claim Window Expiry & Auto-Reassignment `[n8n]`:**
  - n8n sweeper detects entries where `status = 'PROMOTED' AND claim_deadline < NOW()`.
  - Marks record as `EXPIRED_UNCLAIMED`, releases the seat hold, and promotes the next passenger.
* **FR-5.5: Dual-Writer Concurrency Gate `[Both]`:**
  - A gate agent in FastAPI manually assigning a standby seat and the n8n background worker must never allocate the same seat.
  - *Enforcement:* Both systems execute within a Postgres transaction locking the specific seat row:
    ```sql
    SELECT * FROM flight_seats WHERE flight_id = :id AND seat_number = :num FOR UPDATE;
    ```

---

### Domain 6: Scheduled Automations
*Owner: `[n8n]` exclusive direct-to-Postgres*

* **FR-6.1: Timezone-Aware Check-in Reminder:**
  - Hourly n8n cron targets flights departing between 24 and 25 hours from current time.
  - Computes departure time relative to the origin airport's local timezone (e.g., `Asia/Dubai`, `Europe/London`).
  - Sends branded check-in reminder email with boarding pass link via Gmail node.
* **FR-6.2: Cancellation & Disruption Suppression:**
  - Workflow joins `flights` table and checks `status != 'CANCELLED'`.
  - If a flight is cancelled, reminder emails are suppressed.
* **FR-6.3: Deduplicated Price-Drop Alerts:**
  - Compares current route fare against user's alert subscription.
  - Dispatches email only if `current_fare <= target_price` AND `last_alert_sent_at < NOW() - INTERVAL '48 hours'`.
* **FR-6.4: Daily Operations & Load Factor Reporting:**
  - Runs daily at 23:59 UTC.
  - Computes:
    - Total revenue booked per flight.
    - Load Factor (%) = `(total_booked_seats / total_aircraft_capacity) * 100`.
    - Cancellation count.
  - Inserts report into `daily_ops_reports` and dispatches executive digest.

---

### Domain 7: Fraud, Policy & RAG Support (AI Layer)
*Owner: `[n8n]` + Pinecone / Supabase pgvector + Gmail*

* **FR-7.1: Fare-Grounded Policy RAG Pipeline:**
  - Customer submits a policy question via web/email.
  - n8n fetches customer's active booking and exact `fare_class`.
  - Embeds query and queries vector store filtered by `fare_class`.
  - LLM drafts response referencing customer's exact ticket conditions.
* **FR-7.2: Mandatory Human Approval Gate:**
  - *Absolute Rule:* AI-drafted responses are **NEVER** dispatched directly to customers.
  - n8n writes draft to `support_draft_approvals` and notifies Operations Agent via Gmail/dashboard.
  - Email is sent to customer only after agent clicks `[Approve & Dispatch]`.
* **FR-7.3: Bot & Mass-Booking Fraud Scoring:**
  - Scheduled n8n cron scans bookings every 15 minutes.
  - Flags $\ge 5$ bookings from same IP or payment fingerprint in $< 10$ minutes.
  - Computes risk score ($0.0 - 1.0$). If $> 0.85$, marks `SUSPECTED_FRAUD` and alerts fraud team.
* **FR-7.4: Policy Ingestion Pipeline:**
  - Automated workflow chunks markdown policy documents, generates embeddings, and upserts them with metadata (`fare_class`, `version`, `effective_date`).
* **FR-7.5: Historical Fraud Batch Audit:**
  - Weekly scan of past 30 days of bookings to detect chargeback clusters and blacklisted cards.

---

### Domain 8: Approval & Autonomy Boundaries
*Owner: `[Both]` Architectural Contract*

| Action Category | Autonomy Level | Execution Engine | Human Sign-off Required? | Audit Requirement |
| :--- | :--- | :--- | :--- | :--- |
| **Check-in Reminder Email** | Fully Autonomous | n8n | ❌ No | Logged in `notification_logs` |
| **Price Drop Alert** | Fully Autonomous | n8n | ❌ No | Logged in `notification_logs` |
| **Seat Hold TTL Expiry** | Fully Autonomous | FastAPI / n8n | ❌ No | Logged in `seat_holds` |
| **Standard In-Policy Cancellation** | Fully Autonomous | FastAPI | ❌ No | Logged in `admin_audit_logs` |
| **Waitlist Promotion Notice** | Fully Autonomous | n8n | ❌ No | Logged in `waitlist_events` |
| **RAG Support Email Dispatch** | **Restricted / Gate** | n8n + Ops Agent | **✅ YES (Mandatory)** | Draft + Approver ID logged |
| **Schedule-Change Cash Compensation**| **Restricted / Gate** | FastAPI + Finance | **✅ YES (Mandatory)** | Finance Manager ID required |
| **Denied-Boarding Involuntary Bump** | **Restricted / Gate** | FastAPI + Gate Agent | **✅ YES (Mandatory)** | Gate Supervisor ID required |
| **Fraud Cancellation of Booking** | **Restricted / Gate** | n8n + Fraud Analyst | **✅ YES (Mandatory)** | Fraud Analyst ID required |

---

### Domain 9: Shared Database Architecture (The Dual-Writer Contract)
*Owner: `[Both]` / `[n8n]`*

* **FR-9.1: Table Ownership Rules:**
  - `flights`, `flight_classes`, `aircraft`: **Sole Writer: FastAPI**. n8n has Read-Only access.
  - `bookings`, `passengers`, `payments`: **Sole Writer: FastAPI**. n8n has Read-Only access (except status update on fraud flag).
  - `seat_holds`: **Primary Writer: FastAPI** (create/confirm). **Secondary Writer: n8n** (clean expired holds).
  - `waitlists`: **Dual Writer:** FastAPI creates; n8n updates to `PROMOTED` or `EXPIRED`.
  - `notification_logs`, `daily_ops_reports`, `support_draft_approvals`: **Sole Writer: n8n**.
  - `admin_audit_logs`: **Append-Only** by both systems.
* **FR-9.2: Database Invariants:**
  - Capacity Check: `CHECK (first_class_seats + business_class_seats + economy_class_seats = total_capacity)`.
  - Non-Negative Seats: `CHECK (first_class_seats >= 0 AND business_class_seats >= 0 AND economy_class_seats >= 0)`.
  - Booking Limits: `CHECK (booked_seats <= capacity)`.
* **FR-9.3: Concurrency Locking Strategy:**
  - All waitlist promotion and seat-release pollers use `SELECT ... FOR UPDATE SKIP LOCKED`.
* **FR-9.4: Automated Reconciliation Job:**
  - Nightly job verifies `sum(physical_seats_assigned) == booked_seats` and alerts on discrepancies.

---

### Domain 10: Core Infrastructure & Communications
*Owner: `[FastAPI]` & `[n8n]`*

* **FR-10.1: Pydantic V2 Strict Contract:** Complete type validation for all API inputs/outputs.
* **FR-10.2: Idempotency Contract:** Standardized UUIDv4 idempotency key header on all mutating endpoints.
* **FR-10.3: Communication Separation:**
  - **FastAPI:** Synchronous transactional emails (Booking Confirmation, Cancellation Receipt).
  - **n8n:** Asynchronous, batch, and AI emails (Check-in reminders, price drop alerts, human-approved RAG responses).
