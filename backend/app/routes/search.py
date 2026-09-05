"""Flight search, dynamic inventory calculation, and fare rules exploration endpoints."""

from datetime import date as dt_date, datetime, time, timedelta, timezone
import hashlib
import hmac
from typing import Any, Optional, Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.routing import APIRoute
from supabase import AsyncClient

from app.config import settings
from app.dependencies import get_supabase_client
from app.models.responses import (
    FlightSearchResult,
    SeatAvailability,
    SeatDetailResponse,
    SeatMapResponse,
)

router = APIRouter(prefix="/api/v1/flights", tags=["Search"])

# ==============================================================================
# Fare Rules Reference & Policies (PRD Domain 2 / FR-2.2)
# ==============================================================================
FARE_RULES: dict[str, dict[str, Any]] = {
    "BASIC_ECONOMY": {
        "fare_class": "BASIC_ECONOMY",
        "name": "Basic Economy",
        "description": (
            "Lowest price point. Zero free changes, non-refundable "
            "($0 refund or travel credit with penalty), random seat assignment only."
        ),
        "change_policy": "No changes permitted",
        "refund_pct": 0,
        "penalty_pct": 100,
        "seat_selection": "Random assignment only at check-in",
        "priority_boarding": False,
    },
    "FLEXIBLE": {
        "fare_class": "FLEXIBLE",
        "name": "Flexible Economy",
        "description": (
            "Medium price point. Free changes up to 24h prior, refundable "
            "(90% refund minus 10% standard processing fee)."
        ),
        "change_policy": "Free changes permitted up to 24 hours prior to departure",
        "refund_pct": 90,
        "penalty_pct": 10,
        "seat_selection": "Standard seat selection included",
        "priority_boarding": False,
    },
    "PREMIUM_FIRST": {
        "fare_class": "PREMIUM_FIRST",
        "name": "Premium First",
        "description": (
            "Highest price point. 100% fully refundable anytime before departure, "
            "complimentary seat selection, priority waitlist weighting."
        ),
        "change_policy": "Free changes permitted anytime prior to departure",
        "refund_pct": 100,
        "penalty_pct": 0,
        "seat_selection": "Complimentary premium seat selection included",
        "priority_boarding": True,
    },
}


# ==============================================================================
# Helper Functions: Price Lock Token Generation & Verification
# ==============================================================================
def generate_price_lock_token(
    flight_id: str,
    seat_class: str,
    price_cents: int,
    expires_at: Optional[Union[datetime, int]] = None,
) -> tuple[str, datetime]:
    """Generate an HMAC-SHA256 signature using settings.PRICE_LOCK_SECRET.

    The signature is generated over f"{flight_id}:{seat_class}:{price_cents}:{timestamp}".
    Expires in 15 minutes by default.

    Args:
        flight_id: Unique identifier of the flight.
        seat_class: Cabin class (FIRST, BUSINESS, ECONOMY).
        price_cents: Ticket fare price in integer cents USD.
        expires_at: Optional custom expiration timestamp or datetime.

    Returns:
        tuple[str, datetime]: (HMAC-SHA256 hex signature, timezone-aware expiry datetime).
    """
    fid = str(flight_id)
    s_class = str(seat_class).upper()
    cents = int(price_cents)

    if expires_at is None:
        exp_dt = datetime.now(timezone.utc) + timedelta(minutes=15)
        ts = int(exp_dt.timestamp())
    elif isinstance(expires_at, (int, float)):
        ts = int(expires_at)
        exp_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    elif isinstance(expires_at, datetime):
        exp_dt = (
            expires_at
            if expires_at.tzinfo is not None
            else expires_at.replace(tzinfo=timezone.utc)
        )
        ts = int(exp_dt.timestamp())
    else:
        raise ValueError(f"Invalid expires_at parameter: {expires_at}")

    message = f"{fid}:{s_class}:{cents}:{ts}"
    signature = hmac.new(
        settings.PRICE_LOCK_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return signature, exp_dt


def verify_price_lock_token(
    token: str,
    flight_id: str,
    seat_class: str,
    price_cents: int,
    expires_at: Union[datetime, int],
) -> bool:
    """Verify an HMAC-SHA256 price lock token against its payload and expiration window."""
    if not token:
        return False

    now_utc = datetime.now(timezone.utc)
    if isinstance(expires_at, datetime):
        exp_dt = (
            expires_at
            if expires_at.tzinfo is not None
            else expires_at.replace(tzinfo=timezone.utc)
        )
        if now_utc > exp_dt:
            return False
        ts = int(exp_dt.timestamp())
    else:
        ts = int(expires_at)
        if now_utc.timestamp() > ts:
            return False

    fid = str(flight_id)
    s_class = str(seat_class).upper()
    cents = int(price_cents)

    # Validate against expiration timestamp as well as issue timestamp (15 min window)
    for candidate_ts in (ts, ts - 900):
        expected_msg = f"{fid}:{s_class}:{cents}:{candidate_ts}"
        expected_sig = hmac.new(
            settings.PRICE_LOCK_SECRET.encode("utf-8"),
            expected_msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(token, expected_sig):
            return True

    return False


# ==============================================================================
# Endpoints
# ==============================================================================
@router.get("/", summary="Router Health Check")
async def get_search_status() -> dict[str, str]:
    """Check search router readiness."""
    return {"status": "operational", "module": "search"}


@router.get("/fare-rules", summary="Retrieve Airline Fare Class Rules")
async def get_fare_rules() -> dict[str, Any]:
    """Return comprehensive fare class rules, change policies, and cancellation terms."""
    return FARE_RULES


@router.get(
    "/search",
    response_model=list[FlightSearchResult],
    summary="Search Available Flights with Live Inventory & Price Lock",
)
async def search_flights(
    origin: str = Query(
        ...,
        min_length=3,
        max_length=3,
        description="Origin airport 3-letter IATA code",
    ),
    destination: str = Query(
        ...,
        min_length=3,
        max_length=3,
        description="Destination airport 3-letter IATA code",
    ),
    date: dt_date = Query(
        ...,
        description="Flight departure calendar date (YYYY-MM-DD)",
    ),
    seat_class: Optional[str] = Query(
        None,
        description="Optional cabin class filter (FIRST, BUSINESS, ECONOMY)",
    ),
    seat_class_alias: Optional[str] = Query(
        None,
        alias="class",
        include_in_schema=False,
    ),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> list[FlightSearchResult]:
    """Search for flights with live seat availability, active holds deduction, and price lock."""
    clean_origin = origin.strip().upper()
    clean_destination = destination.strip().upper()

    if clean_origin == clean_destination:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Origin and destination airports must be distinct",
        )

    requested_class = seat_class or seat_class_alias
    filter_class: Optional[str] = (
        requested_class.strip().upper() if requested_class else None
    )

    if filter_class and filter_class not in {"FIRST", "BUSINESS", "ECONOMY"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid seat class '{requested_class}'. Must be one of: FIRST, BUSINESS, ECONOMY",
        )

    start_dt = datetime.combine(date, time.min).replace(tzinfo=timezone.utc)
    end_dt = datetime.combine(date + timedelta(days=1), time.min).replace(
        tzinfo=timezone.utc
    )

    # Query flights joined with flight_classes matching route and departure date window
    flights_res = (
        await supabase.table("flights")
        .select("*, flight_classes(*)")
        .eq("origin_airport", clean_origin)
        .eq("destination_airport", clean_destination)
        .gte("departure_time", start_dt.isoformat())
        .lt("departure_time", end_dt.isoformat())
        .neq("status", "CANCELLED")
        .order("departure_time")
        .execute()
    )

    flights_data = flights_res.data or []
    if not flights_data:
        return []

    now_iso = datetime.now(timezone.utc).isoformat()
    results: list[FlightSearchResult] = []

    for flight in flights_data:
        flight_id = flight["id"]
        raw_classes = flight.get("flight_classes") or []

        # Filter classes if specific seat_class was requested
        if filter_class:
            matched_classes = [
                c
                for c in raw_classes
                if str(c.get("seat_class", "")).upper() == filter_class
            ]
            if not matched_classes:
                continue
        else:
            matched_classes = raw_classes

        if not matched_classes:
            continue

        # Count active holds for this flight:
        # SELECT count(*) FROM seat_holds WHERE flight_id=:id AND status='HELD' AND expires_at > NOW()
        holds_res = (
            await supabase.table("seat_holds")
            .select("id, seat_id, flight_seats(seat_class)")
            .eq("flight_id", flight_id)
            .eq("status", "HELD")
            .gt("expires_at", now_iso)
            .execute()
        )
        holds_data = holds_res.data or []

        # Aggregate active holds count per cabin class
        active_holds_by_class: dict[str, int] = {}
        for hold in holds_data:
            fs = hold.get("flight_seats") or {}
            sc = fs.get("seat_class")
            if sc:
                sc_upper = str(sc).upper()
                active_holds_by_class[sc_upper] = (
                    active_holds_by_class.get(sc_upper, 0) + 1
                )

        availability_list: list[SeatAvailability] = []
        for fc in matched_classes:
            c_name = str(fc.get("seat_class", "")).upper()
            capacity = int(fc.get("capacity", 0))
            booked_seats = int(fc.get("booked_seats", 0))
            active_holds_count = active_holds_by_class.get(c_name, 0)

            # Invariant: available_seats = max(0, capacity - booked_seats - active_holds)
            available_seats = max(0, capacity - booked_seats - active_holds_count)
            base_price_cents = int(fc.get("base_price_cents", 0))

            availability_list.append(
                SeatAvailability(
                    seat_class=c_name,
                    total_capacity=capacity,
                    available_seats=available_seats,
                    base_price_cents=base_price_cents,
                )
            )

        if not availability_list:
            continue

        # Select target class and base fare to seal 15-minute price lock token
        target_avail = None
        if filter_class:
            target_avail = next(
                (a for a in availability_list if a.seat_class == filter_class), None
            )
        if not target_avail:
            target_avail = next(
                (a for a in availability_list if a.seat_class == "ECONOMY"),
                min(availability_list, key=lambda a: a.base_price_cents),
            )

        token, expires_at = generate_price_lock_token(
            flight_id=str(flight_id),
            seat_class=target_avail.seat_class,
            price_cents=target_avail.base_price_cents,
        )

        dep_time = flight["departure_time"]
        dep_time_dt = (
            datetime.fromisoformat(dep_time)
            if isinstance(dep_time, str)
            else dep_time
        )

        arr_time = flight["arrival_time"]
        arr_time_dt = (
            datetime.fromisoformat(arr_time)
            if isinstance(arr_time, str)
            else arr_time
        )

        results.append(
            FlightSearchResult(
                flight_id=UUID(flight_id),
                flight_number=flight["flight_number"],
                origin=flight["origin_airport"],
                destination=flight["destination_airport"],
                departure_time=dep_time_dt,
                arrival_time=arr_time_dt,
                availability=availability_list,
                price_lock_token=token,
                price_lock_expires_at=expires_at,
            )
        )

    return results


@router.get(
    "/{id}/seats",
    response_model=SeatMapResponse,
    summary="Get Physical Seat Map Layout and Live Availability",
)
async def get_flight_seats(
    id: UUID,
    supabase: AsyncClient = Depends(get_supabase_client),
) -> SeatMapResponse:
    """Return physical seat map for a flight. If a seat's hold has expired, display it as AVAILABLE."""
    flight_id_str = str(id)

    # 1. Verify flight existence
    flight_res = (
        await supabase.table("flights")
        .select("id")
        .eq("id", flight_id_str)
        .maybe_single()
        .execute()
    )
    if not flight_res or not flight_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Flight {id} not found",
        )

    # 2. Query physical seats configured for this flight
    seats_res = (
        await supabase.table("flight_seats")
        .select("*")
        .eq("flight_id", flight_id_str)
        .order("seat_number")
        .execute()
    )
    seats_data = seats_res.data or []

    # 3. Query active unexpired holds for this flight
    now_iso = datetime.now(timezone.utc).isoformat()
    holds_res = (
        await supabase.table("seat_holds")
        .select("seat_id")
        .eq("flight_id", flight_id_str)
        .eq("status", "HELD")
        .gt("expires_at", now_iso)
        .execute()
    )
    active_held_seat_ids = {
        h["seat_id"] for h in (holds_res.data or []) if h.get("seat_id")
    }

    # 4. Map effective seat status: if a hold has expired, display as AVAILABLE
    seat_details: list[SeatDetailResponse] = []
    for seat in seats_data:
        raw_status = str(seat.get("status", "AVAILABLE")).upper()
        seat_id = seat.get("id")

        if raw_status == "BOOKED":
            effective_status = "BOOKED"
        elif raw_status == "BLOCKED":
            effective_status = "BLOCKED"
        elif seat_id in active_held_seat_ids:
            effective_status = "HELD"
        elif raw_status == "HELD":
            # Hold has expired without being swept or completed
            effective_status = "AVAILABLE"
        else:
            effective_status = "AVAILABLE"

        seat_details.append(
            SeatDetailResponse(
                id=UUID(seat["id"]),
                seat_number=seat["seat_number"],
                seat_class=str(seat.get("seat_class", "ECONOMY")).upper(),
                status=effective_status,
            )
        )

    return SeatMapResponse(flight_id=id, seats=seat_details)


# ==============================================================================
# Dual-Prefix Compatibility Registration
# Allows router to be mounted at root or with prefix="/api/v1" without breaking
# ==============================================================================
router.routes.append(
    APIRoute(
        "/flights/search",
        search_flights,
        methods=["GET"],
        response_model=list[FlightSearchResult],
        tags=["Search"],
        include_in_schema=False,
    )
)
router.routes.append(
    APIRoute(
        "/flights/{id}/seats",
        get_flight_seats,
        methods=["GET"],
        response_model=SeatMapResponse,
        tags=["Search"],
        include_in_schema=False,
    )
)
router.routes.append(
    APIRoute(
        "/flights/fare-rules",
        get_fare_rules,
        methods=["GET"],
        tags=["Search"],
        include_in_schema=False,
    )
)
router.routes.append(
    APIRoute(
        "/flights",
        get_search_status,
        methods=["GET"],
        tags=["Search"],
        include_in_schema=False,
    )
)
router.routes.append(
    APIRoute(
        "/flights/",
        get_search_status,
        methods=["GET"],
        tags=["Search"],
        include_in_schema=False,
    )
)

