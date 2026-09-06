# 🎤 3-Minute Master Hackathon Demo Script & Judge Walkthrough
**Flight Management System (FMS)**  
**Stack**: Next.js 14 + FastAPI + Supabase Postgres + n8n Cloud + Pinecone RAG  

---

## ⏱ Quick Run Checklist Before Presenting
1. **FastAPI Backend**:
   ```bash
   cd "e:\smit hackathon\project\backend"
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```
2. **Next.js Frontend**:
   ```bash
   cd "e:\smit hackathon\frontend"
   npm run dev
   ```
   *Open: [http://localhost:3000](http://localhost:3000)*

---

## 🎙 Spoken Script: 3-Minute Judge Presentation

### [0:00 - 0:45] The Problem & Dual-Writer Architecture
> "Hello judges! Airline reservation systems face the classic distributed dual-writer problem: live passenger bookings demand sub-second ACID transactions, while scheduled automation and background agents need direct database access without corrupting inventory.
> 
> Our system solves this with **FastAPI** as the exclusive synchronous write-path for live bookings, **n8n Cloud** for asynchronous background sweeps, and **Supabase Postgres** with row-level locks (`FOR UPDATE SKIP LOCKED`) and database check invariants as the supreme single source of truth."

---

### [0:45 - 1:30] Journey 1 & 2: Live Search, 15-Min Price Lock & Atomic 10-Min Hold
> "Let's demonstrate **Journey 1: Search & Price Lock**.
> On the passenger portal, we search **LHR to DXB**.
> Notice two critical things:
> 1. Real-time class seat counts are computed directly from `capacity - (booked + active_holds)`.
> 2. We generate an HMAC-SHA256 **Price Lock Token** that seals the fare for **15 minutes**.
>
> Now, **Journey 2: Atomic Checkout**.
> Clicking 'Select Seat' loads the physical seat layout. When I select seat **14A** and click **Hold Seat**, FastAPI acquires an atomic row lock and establishes a **10-minute hold timer** powered by a TTL sweep worker.
> I enter my details, click **Confirm Booking**, and within milliseconds, FastAPI validates the price lock signature, confirms the booking via database RPC, persists an idempotent transaction ledger, and returns our confirmed **PNR reference**."

---

### [1:30 - 2:15] Journey 3: Tiered Refund Policy & Standby Waitlist
> "Next, **Journey 3: Policy Branching & Cancellations**.
> Let's go to **Manage Booking** and lookup our PNR.
> When a passenger cancels:
> - **Basic Economy** strictly issues **\$0 refund**.
> - **Flexible** grants a **90% refund** or **100% travel credit voucher**.
> - **Premium First** gives a **100% instant refund**.
> 
> Let's click 'Cancel for Refund'—the backend releases the held seat back to the inventory, recalculates the refund, and if the flight was full, our **n8n waitlist auto-promotion worker** immediately promotes the next passenger using `SELECT ... FOR UPDATE SKIP LOCKED`!"

---

### [2:15 - 3:00] Journey 4: Ops Admin & Grounded AI Support (RAG)
> "Finally, **Journey 4: Ops Admin & AI Support Gate**.
> In the **Admin Portal**:
> 1. **Mathematical Invariant Gate**: Creating a 100-passenger flight enforces `First (20) + Business (30) + Economy (50) == Total (100)`. Any invalid sum is rejected with HTTP 422.
> 2. **Cascading Schedule Shift**: A delay over 180 minutes automatically flags all passenger tickets for free rebooking.
> 3. **AI Support with Human-in-the-Loop**: When a customer asks: *'Can I cancel and get my money back?'*, our Gemini RAG pipeline vector-searches our airline policy embeddings in Pinecone, grounds the answer against the passenger's exact ticket fare class, and queues a draft response for Ops Agent approval before sending.
>
> Thank you! All 10 domains and 56 specifications are live, verified, and ready for Q&A."

---

## 🛠 Direct curl Commands for Deep-Dive Judge Verification

### 1. Invariant Validation (Admin Flight Creation)
```bash
curl -X POST "http://localhost:8000/api/v1/admin/flights" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Role: SUPER_ADMIN" \
  -d '{
    "flight_number": "AL888",
    "origin_airport": "LHR",
    "destination_airport": "DXB",
    "departure_time": "2026-10-15T08:00:00Z",
    "arrival_time": "2026-10-15T18:00:00Z",
    "total_capacity": 100,
    "first_class_seats": 20,
    "business_class_seats": 30,
    "economy_class_seats": 50,
    "origin_tz": "UTC"
  }'
```

### 2. Live Search with HMAC Price Lock
```bash
curl -X GET "http://localhost:8000/api/v1/flights/search?origin=LHR&destination=DXB&date=2026-10-15"
```

### 3. Idempotent Booking Confirmation
```bash
curl -X POST "http://localhost:8000/api/v1/bookings/confirm" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: DEMO-IDEMP-001" \
  -d '{
    "hold_id": "<HOLD_ID>",
    "passenger_name": "Jane Doe",
    "passenger_email": "jane@example.com",
    "fare_class": "FLEXIBLE",
    "fare_cents": 85000,
    "price_lock_token": "<TOKEN>"
  }'
```

### 4. Background Automations & Webhooks
```bash
curl -X POST "http://localhost:8000/api/v1/webhooks/trigger-hold-sweep"
curl -X POST "http://localhost:8000/api/v1/webhooks/trigger-waitlist-promotion"
curl -X POST "http://localhost:8000/api/v1/webhooks/trigger-fraud-scan"
```
