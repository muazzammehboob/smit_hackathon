# FRONTEND_API_GUIDE.md

This document serves as the comprehensive API contract and frontend integration guide for the Flight Management System.

## 1. BASE CONFIGURATION & ENVIRONMENTS

### Base URLs
- **Local API Base URL:** `http://localhost:8000`
- **Cloud API Base URL:** `https://<your-project>.fastapicloud.app`

### Documentation
- **Interactive Swagger Docs:** `/docs`
- **OpenAPI Schema:** `/openapi.json`

### Required Headers
- **Standard Requests:** `Content-Type: application/json`
- **Checkout Mutations (Hold & Confirm):** `Idempotency-Key: <unique-uuid>`
- **Admin Endpoints:** `X-Admin-Role: SUPER_ADMIN` (or `OPS_AGENT`)

---

## 2. READY-TO-USE TYPESCRIPT INTERFACES

Add these interfaces directly to your `types.ts` file.

```typescript
export interface Flight {
  id: string;
  flight_number: string;
  origin: string;
  destination: string;
  departure_time: string; // ISO 8601
  arrival_time: string; // ISO 8601
  status: 'SCHEDULED' | 'DELAYED' | 'CANCELLED';
}

export interface SeatAvailability {
  basic_economy: number;
  flexible: number;
  premium_first: number;
}

export interface FlightSearchResult {
  flight: Flight;
  available_seats: SeatAvailability;
  fares: {
    basic_economy: number;
    flexible: number;
    premium_first: number;
  };
  price_lock_token: string; // 15-min HMAC lock
}

export interface PhysicalSeat {
  seat_number: string; // e.g. "1A"
  seat_class: 'BASIC_ECONOMY' | 'FLEXIBLE' | 'PREMIUM_FIRST';
  status: 'AVAILABLE' | 'HELD' | 'BOOKED';
}

export interface SeatHoldRequest {
  flight_id: string;
  seat_number: string;
  passenger_name: string;
}

export interface HoldResponse {
  hold_id: string;
  expires_at: string; // ISO 8601
  status: 'HELD';
}

export interface BookingConfirmRequest {
  hold_id: string;
  price_lock_token: string;
  payment_method: string;
}

export interface BookingResponse {
  pnr: string; // 6-character code
  status: 'CONFIRMED';
  ticket_details: any;
}

export interface CancellationResponse {
  status: 'CANCELLED';
  refund_amount: number;
  currency: string;
}

export interface TravelCredit {
  voucher_code: string;
  amount: number;
  currency: string;
  expires_at: string; // 365 days from issue
}

export interface WaitlistEntry {
  id: string;
  flight_id: string;
  email: string;
  priority_score: number;
  status: 'WAITING' | 'PROMOTED' | 'CLAIMED';
}

export interface WaitlistJoinRequest {
  flight_id: string;
  email: string;
  seat_class: string;
}

export interface WaitlistResponse {
  entry: WaitlistEntry;
  current_position: number;
}

export interface SupportInquiryRequest {
  pnr: string;
  question: string;
}

export interface SupportDraftResponse {
  draft_id: string;
  suggested_answer: string;
  confidence_score: number;
}

export interface AdminAuditLog {
  id: string;
  action: string;
  performed_by: string;
  timestamp: string;
  details: Record<string, any>;
}
```

---

## 3. ENDPOINT DIRECTORY (BY USER FLOW)

### FLOW 1: FLIGHT SEARCH & SEAT MAP

**Search Flights**
- **Method:** `GET`
- **Path:** `/api/v1/flights/search`
- **Query Params:** `origin=LHR&destination=DXB&date=2026-10-01`
- **Response:** Array of `FlightSearchResult`
- **Notes:** Returns available seats per class, fare descriptions, and a 15-min HMAC `price_lock_token`.

**Get Physical Seat Map**
- **Method:** `GET`
- **Path:** `/api/v1/flights/{id}/seats`
- **Response:** Array of `PhysicalSeat` (e.g., 1A..30F) with current statuses.

### FLOW 2: ATOMIC SEAT HOLD & CHECKOUT

**Hold a Seat**
- **Method:** `POST`
- **Path:** `/api/v1/bookings/hold`
- **Headers:** `Idempotency-Key`
- **Body:** `SeatHoldRequest`
- **Response:** `HoldResponse`
- **Notes:** Locks seat for 10 minutes using row-level locks.

**Confirm Booking**
- **Method:** `POST`
- **Path:** `/api/v1/bookings/confirm`
- **Headers:** `Idempotency-Key`
- **Body:** `BookingConfirmRequest`
- **Response:** `BookingResponse`
- **Notes:** Validates `price_lock_token`, records `Idempotency-Key`, confirms booking, and returns a 6-character PNR.

**Get Booking Details by PNR**
- **Method:** `GET`
- **Path:** `/api/v1/bookings/{pnr}`
- **Response:** `BookingResponse`

### FLOW 3: CANCELLATIONS & TRAVEL CREDITS

**Cancel Entire Booking**
- **Method:** `POST`
- **Path:** `/api/v1/bookings/{pnr}/cancel`
- **Response:** `CancellationResponse`
- **Notes:** Fare-branching rules apply (Basic Economy $0; Flexible 90%; Premium First 100%).

**Cancel Specific Passenger (Split)**
- **Method:** `POST`
- **Path:** `/api/v1/bookings/{pnr}/cancel-passenger`
- **Response:** `CancellationResponse`
- **Notes:** Partial split for multi-passenger bookings.

**Convert to Travel Credit**
- **Method:** `POST`
- **Path:** `/api/v1/bookings/{pnr}/travel-credit`
- **Response:** `TravelCredit`
- **Notes:** Issues a 365-day travel voucher code.

### FLOW 4: WAITLIST QUEUE

**Join Waitlist**
- **Method:** `POST`
- **Path:** `/api/v1/waitlist/join`
- **Body:** `WaitlistJoinRequest`
- **Response:** `WaitlistResponse`
- **Notes:** Only allowed when seats == 0. Calculates priority score internally.

**Check Waitlist Position**
- **Method:** `GET`
- **Path:** `/api/v1/waitlist/position`
- **Query Params:** `flight_id={id}&email={email}`
- **Response:** `{ "position": 3 }`

**Leave Waitlist**
- **Method:** `DELETE`
- **Path:** `/api/v1/waitlist/{id}`
- **Response:** `{ "status": "REMOVED" }`

**Claim Waitlist Seat**
- **Method:** `POST`
- **Path:** `/api/v1/waitlist/{id}/claim`
- **Response:** `BookingResponse`
- **Notes:** Used when a promoted passenger confirms their seat.

### FLOW 5: GOOGLE GEMINI AI CUSTOMER SUPPORT (RAG)

**Inquire with AI**
- **Method:** `POST`
- **Path:** `/api/v1/support/inquire`
- **Body:** `SupportInquiryRequest` (`{"pnr": "ABC123", "question": "..."}`)
- **Response:** `SupportDraftResponse`
- **Notes:** AI retrieves policy chunk from pgvector and drafts grounded answer.

**Approve AI Draft**
- **Method:** `POST`
- **Path:** `/api/v1/support/approve`
- **Body:** `{"draft_id": "...", "action": "approve" | "reject"}`
- **Response:** `{ "status": "APPROVED" }`

### FLOW 6: ADMIN OPERATIONS CONSOLE

**Create Flight**
- **Method:** `POST`
- **Path:** `/api/v1/admin/flights`
- **Headers:** `X-Admin-Role: SUPER_ADMIN`
- **Response:** `Flight`
- **Notes:** Validates capacities, generates seat layout.

**List All Flights**
- **Method:** `GET`
- **Path:** `/api/v1/admin/flights`
- **Headers:** `X-Admin-Role: SUPER_ADMIN`
- **Response:** Array of flights with live load factors.

**Adjust Class Capacity**
- **Method:** `PATCH`
- **Path:** `/api/v1/admin/flights/{id}/capacity`
- **Headers:** `X-Admin-Role: SUPER_ADMIN`
- **Response:** `Flight`

**Shift Schedule**
- **Method:** `PATCH`
- **Path:** `/api/v1/admin/flights/{id}/schedule`
- **Headers:** `X-Admin-Role: SUPER_ADMIN`
- **Response:** `Flight`
- **Notes:** If > 180 min, cascades full-refund override.

**Cancel Flight**
- **Method:** `POST`
- **Path:** `/api/v1/admin/flights/{id}/cancel`
- **Headers:** `X-Admin-Role: SUPER_ADMIN`
- **Response:** `{ "status": "CANCELLED" }`
- **Notes:** Master cancellation.

**Get Audit Logs**
- **Method:** `GET`
- **Path:** `/api/v1/admin/audit-logs`
- **Headers:** `X-Admin-Role: SUPER_ADMIN`
- **Response:** Paginated array of `AdminAuditLog`

### FLOW 7: DEMO AUTOMATION TRIGGERS

**Trigger Hold Sweep**
- **Method:** `POST`
- **Path:** `/api/v1/webhooks/trigger-hold-sweep`
- **Response:** `{ "status": "SUCCESS", "cleared": 5 }`
- **Notes:** Instantly runs expired hold sweeper.

**Trigger Waitlist Promotion**
- **Method:** `POST`
- **Path:** `/api/v1/webhooks/trigger-waitlist-promotion`
- **Response:** `{ "status": "SUCCESS", "promoted": 2 }`
- **Notes:** Instantly runs waitlist promoter.

**Trigger Fraud Scan**
- **Method:** `POST`
- **Path:** `/api/v1/webhooks/trigger-fraud-scan`
- **Response:** `{ "status": "SUCCESS", "flagged": 0 }`
- **Notes:** Instantly runs bot fraud detection.

---

## 4. SAMPLE REACT / NEXT.JS API CLIENT (`api.ts`)

```typescript
// api.ts
import { v4 as uuidv4 } from 'uuid';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function fetchAPI<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  // Automatically inject Idempotency-Key for POST/PATCH requests if missing
  if (['POST', 'PATCH'].includes(options.method?.toUpperCase() || '') && !headers['Idempotency-Key']) {
    headers['Idempotency-Key'] = uuidv4();
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(errorBody.detail || `API Request failed: ${response.statusText}`);
  }

  return response.json();
}

// Example usage
export const flightApi = {
  searchFlights: (origin: string, destination: string, date: string) =>
    fetchAPI<FlightSearchResult[]>(`/api/v1/flights/search?origin=${origin}&destination=${destination}&date=${date}`),

  holdSeat: (data: SeatHoldRequest) =>
    fetchAPI<HoldResponse>('/api/v1/bookings/hold', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};
```

---

## 5. UI SCREEN BLUEPRINTS

For the hackathon demo, we recommend the following clean 4-tab layout:

### Tab 1: Passenger Booking
- **Search -> Seat Map -> Checkout -> PNR Ticket**
- Users can enter origin, destination, and dates.
- Interactive seat map displaying statuses (AVAILABLE, HELD, BOOKED).
- Checkout form using the atomic hold & confirm endpoints.
- Displays the generated 6-character PNR ticket.

### Tab 2: Manage Booking & Waitlist
- **Cancel ticket / View refund / Claim waitlist seat**
- Users can input their PNR to retrieve booking details.
- Allows full or partial cancellations with fare rule calculation.
- Options to request a Travel Credit or standard refund.
- Promoted waitlist passengers can claim their seat here.

### Tab 3: AI Customer Support
- **Inquiry chat with Human Approval Gate toggle**
- A chat interface powered by Gemini AI and pgvector.
- Passengers submit questions related to their PNR and policies.
- Includes a toggle (for demo purposes) to switch between auto-reply and human-approval gates for AI-generated drafts.

### Tab 4: Admin & Ops Console
- **Flight CRUD, Schedule Shift, Audit Logs, Fraud Alerts**
- Dedicated view for OPS_AGENT or SUPER_ADMIN.
- Modify flight capacity, trigger large schedule shifts.
- Master cancellations.
- View live audit logs and trigger manual fraud/hold sweepers.
