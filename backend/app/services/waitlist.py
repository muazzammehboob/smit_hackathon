"""Waitlist domain service handling priority queueing, position tracking, and seat promotion claims."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional, Union
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from app.models.requests import WaitlistJoinRequest
from app.models.responses import WaitlistResponse

logger = logging.getLogger("app.services.waitlist")


def compute_priority_score(
    loyalty_tier: Union[str, Any],
    fare_or_seat_class: Union[str, Any],
    timestamp: Optional[datetime] = None,
) -> int:
    """Compute deterministic waitlist priority score.

    Tier weights: PLATINUM=3, GOLD=2, SILVER=1, GENERAL=0.
    Fare/Seat weights: PREMIUM_FIRST/FIRST=3, FLEXIBLE/BUSINESS=2, BASIC_ECONOMY/ECONOMY=1.
    Formula uses positive scale ensuring higher tier & earlier request always wins.
    """
    tier_weights = {
        "PLATINUM": 3,
        "GOLD": 2,
        "SILVER": 1,
        "GENERAL": 0,
    }
    fare_weights = {
        "PREMIUM_FIRST": 3,
        "FIRST": 3,
        "FLEXIBLE": 2,
        "BUSINESS": 2,
        "BASIC_ECONOMY": 1,
        "ECONOMY": 1,
    }

    tier_str = (
        loyalty_tier.value if hasattr(loyalty_tier, "value") else str(loyalty_tier)
    ).upper()
    tier_weight = tier_weights.get(tier_str, 0)

    fare_str = (
        fare_or_seat_class.value
        if hasattr(fare_or_seat_class, "value")
        else str(fare_or_seat_class)
    ).upper()
    fare_weight = fare_weights.get(fare_str, 1)

    now_dt = timestamp or datetime.now(timezone.utc)
    epoch_hours = int(now_dt.timestamp() // 3600)

    # Scale: (tier * 1,000,000) + (fare * 10,000) + (100,000,000 - epoch_hours)
    # Guarantees higher tier beats lower tier, higher fare beats lower fare,
    # and earlier request (smaller epoch_hours) beats later request.
    return (tier_weight * 1000000) + (fare_weight * 10000) + (100000000 - epoch_hours)


async def join_waitlist_service(
    supabase: Any,
    payload: WaitlistJoinRequest,
) -> dict[str, Any]:
    """Join priority standby waitlist for a full flight class.

    1. Checks flight existence in flights table (HTTP 404 if missing).
    2. Checks seat availability: available = capacity - (booked_seats + active_holds_count).
       Enforces available == 0. Raises HTTPException(400) if available > 0.
    3. Computes deterministic priority_score.
    4. Inserts record into waitlist table.
    5. Queries current queue position and returns conforming waitlist payload.
    """
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase database client is unavailable",
        )

    if not payload.flight_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="flight_id is required",
        )

    flight_id_str = str(payload.flight_id)

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
            detail="Flight not found",
        )

    # 2. Check seat availability for requested_class
    class_str = (
        payload.requested_class.value
        if hasattr(payload.requested_class, "value")
        else str(payload.requested_class)
    ).upper()

    class_res = (
        await supabase.table("flight_classes")
        .select("capacity, booked_seats")
        .eq("flight_id", flight_id_str)
        .eq("seat_class", class_str)
        .maybe_single()
        .execute()
    )
    if not class_res or not class_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Flight class {class_str} not found for flight",
        )

    capacity = int(class_res.data.get("capacity", 0))
    booked_seats = int(class_res.data.get("booked_seats", 0))

    # Count active holds for this flight in the requested class
    now_iso = datetime.now(timezone.utc).isoformat()
    holds_res = (
        await supabase.table("seat_holds")
        .select("id, flight_seats(seat_class)")
        .eq("flight_id", flight_id_str)
        .eq("status", "HELD")
        .gt("expires_at", now_iso)
        .execute()
    )
    holds_data = holds_res.data or []
    active_holds_count = sum(
        1
        for hold in holds_data
        if (hold.get("flight_seats") or {}).get("seat_class", "").upper() == class_str
    )

    available_seats = capacity - (booked_seats + active_holds_count)
    if available_seats > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seats are still available for booking, cannot join waitlist",
        )

    # 3. Compute priority score
    loyalty_str = (
        payload.loyalty_tier.value
        if hasattr(payload.loyalty_tier, "value")
        else str(payload.loyalty_tier)
    ).upper()

    fare_str = class_str
    priority_score = compute_priority_score(
        loyalty_tier=loyalty_str,
        fare_or_seat_class=fare_str,
    )

    # Check for duplicate
    passenger_email = str(payload.passenger_email).strip()
    existing = (
        await supabase.table("waitlist")
        .select("id")
        .eq("flight_id", flight_id_str)
        .eq("passenger_email", passenger_email)
        .eq("status", "WAITING")
        .maybe_single()
        .execute()
    )

    if existing and existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Passenger is already on the waitlist for this flight",
        )

    # 4. Insert record into waitlist table
    insert_payload = {
        "flight_id": flight_id_str,
        "passenger_name": payload.passenger_name.strip(),
        "passenger_email": passenger_email,
        "loyalty_tier": loyalty_str,
        "requested_class": class_str,
        "priority_score": priority_score,
        "status": "WAITING",
    }

    insert_res = await supabase.table("waitlist").insert(insert_payload).execute()
    inserted_data = insert_res.data
    if isinstance(inserted_data, list) and inserted_data:
        inserted_entry = dict(inserted_data[0])
    elif isinstance(inserted_data, dict):
        inserted_entry = dict(inserted_data)
    else:
        inserted_entry = dict(insert_payload)
        inserted_entry["id"] = str(uuid4())

    waitlist_id = str(inserted_entry.get("id") or inserted_entry.get("waitlist_id"))

    # 5. Query current queue position:
    # count of entries in waitlist for same flight_id with status='WAITING' and priority_score > my_score + 1
    pos_res = (
        await supabase.table("waitlist")
        .select("id", count="exact")
        .eq("flight_id", flight_id_str)
        .eq("status", "WAITING")
        .gt("priority_score", priority_score)
        .execute()
    )
    higher_count = (
        pos_res.count
        if hasattr(pos_res, "count") and pos_res.count is not None
        else len(pos_res.data or [])
    )
    position = higher_count + 1

    return {
        "waitlist_id": waitlist_id,
        "flight_id": flight_id_str,
        "passenger_name": payload.passenger_name.strip(),
        "passenger_email": str(payload.passenger_email).strip(),
        "loyalty_tier": loyalty_str,
        "requested_class": class_str,
        "priority_score": priority_score,
        "status": "WAITING",
        "position": position,
    }


async def get_waitlist_position_service(
    supabase: Any,
    flight_id: str,
    email: str,
) -> dict[str, Any]:
    """Look up active waitlist entry and compute its current queue position."""
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase database client is unavailable",
        )

    clean_flight_id = str(flight_id).strip()
    clean_email = str(email).strip()

    entry_res = (
        await supabase.table("waitlist")
        .select("*")
        .eq("flight_id", clean_flight_id)
        .eq("passenger_email", clean_email)
        .eq("status", "WAITING")
        .maybe_single()
        .execute()
    )

    if not entry_res or not entry_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active waitlist entry not found",
        )

    entry = dict(entry_res.data)
    my_score = int(entry.get("priority_score", 0))

    pos_res = (
        await supabase.table("waitlist")
        .select("id", count="exact")
        .eq("flight_id", clean_flight_id)
        .eq("status", "WAITING")
        .gt("priority_score", my_score)
        .execute()
    )
    higher_count = (
        pos_res.count
        if hasattr(pos_res, "count") and pos_res.count is not None
        else len(pos_res.data or [])
    )
    position = higher_count + 1

    entry["position"] = position
    if "id" in entry and "waitlist_id" not in entry:
        entry["waitlist_id"] = entry["id"]

    return entry


async def leave_waitlist_service(
    supabase: Any,
    waitlist_id: str,
) -> dict[str, Any]:
    """Remove a passenger from waitlist by setting status to CANCELLED."""
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase database client is unavailable",
        )

    clean_id = str(waitlist_id).strip()

    entry_res = (
        await supabase.table("waitlist")
        .select("*")
        .eq("id", clean_id)
        .maybe_single()
        .execute()
    )

    if not entry_res or not entry_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waitlist entry not found",
        )

    await (
        supabase.table("waitlist")
        .update({"status": "CANCELLED"})
        .eq("id", clean_id)
        .execute()
    )

    return {
        "waitlist_id": clean_id,
        "status": "CANCELLED",
        "message": "Successfully left the waitlist",
    }


async def claim_waitlist_seat_service(
    supabase: Any,
    waitlist_id: str,
    client_ip: str = "127.0.0.1",
) -> dict[str, Any]:
    """Convert a promoted waitlist hold into a confirmed booking.

    1. Checks waitlist entry existence (HTTP 404 if missing).
    2. Verifies status is 'PROMOTED' (HTTP 400 if not).
    3. Verifies claim_deadline: if expired, marks 'EXPIRED' and raises HTTP 400.
    4. Executes confirm_booking RPC.
    5. Updates waitlist record to 'CONVERTED'.
    6. Returns confirmed booking details.
    """
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase database client is unavailable",
        )

    clean_id = str(waitlist_id).strip()

    entry_res = (
        await supabase.table("waitlist")
        .select("*")
        .eq("id", clean_id)
        .maybe_single()
        .execute()
    )

    if not entry_res or not entry_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waitlist entry not found",
        )

    waitlist_entry = dict(entry_res.data)

    # Verify status is 'PROMOTED'
    if waitlist_entry.get("status") != "PROMOTED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Waitlist entry is not promoted for claiming",
        )

    # Verify claim deadline
    raw_deadline = waitlist_entry.get("claim_deadline")
    if raw_deadline:
        if isinstance(raw_deadline, str):
            deadline_dt = datetime.fromisoformat(raw_deadline.replace("Z", "+00:00"))
        else:
            deadline_dt = raw_deadline

        if deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)

        now_utc = datetime.now(timezone.utc)
        if deadline_dt < now_utc:
            try:
                await (
                    supabase.table("waitlist")
                    .update({"status": "EXPIRED"})
                    .eq("id", clean_id)
                    .execute()
                )
            except Exception:
                await (
                    supabase.table("waitlist")
                    .update({"status": "EXPIRED_UNCLAIMED"})
                    .eq("id", clean_id)
                    .execute()
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Waitlist claim window has expired",
            )

    hold_id = waitlist_entry.get("hold_id")
    if not hold_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Promoted waitlist entry has no associated seat hold",
        )

    # Map requested class to fare class
    req_class = str(waitlist_entry.get("requested_class", "ECONOMY")).upper()
    fare_class_map = {
        "FIRST": "PREMIUM_FIRST",
        "BUSINESS": "FLEXIBLE",
        "ECONOMY": "BASIC_ECONOMY",
    }
    fare_class_str = fare_class_map.get(req_class, "BASIC_ECONOMY")

    fare_cents = 0
    try:
        fc_res = (
            await supabase.table("flight_classes")
            .select("base_price_cents")
            .eq("flight_id", str(waitlist_entry.get("flight_id")))
            .eq("seat_class", req_class)
            .maybe_single()
            .execute()
        )
        if fc_res and fc_res.data and fc_res.data.get("base_price_cents"):
            fare_cents = int(fc_res.data["base_price_cents"])
    except Exception as exc:
        logger.debug("Failed to lookup base_price_cents for claim: %s", exc)

    if fare_cents <= 0:
        fare_cents = 25000

    rpc_params = {
        "p_hold_id": str(hold_id),
        "p_passenger_name": waitlist_entry.get("passenger_name", "").strip(),
        "p_passenger_email": str(waitlist_entry.get("passenger_email", "")).strip(),
        "p_fare_class": fare_class_str,
        "p_fare_cents": fare_cents,
        "p_ip": client_ip or "127.0.0.1",
    }

    try:
        rpc_response = await supabase.rpc("confirm_booking", rpc_params).execute()
    except HTTPException:
        raise
    except Exception as exc:
        err_msg = str(exc).lower()
        if "expired" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Seat hold has expired",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Booking confirmation failed: {str(exc)}",
        )

    raw_data = rpc_response.data if rpc_response else None
    if isinstance(raw_data, str):
        try:
            booking_data = json.loads(raw_data)
        except Exception:
            booking_data = {"booking_id": raw_data}
    elif isinstance(raw_data, dict):
        booking_data = dict(raw_data)
    elif isinstance(raw_data, list) and raw_data:
        booking_data = dict(raw_data[0])
    else:
        booking_data = {}

    # Transition waitlist entry to CONVERTED
    await (
        supabase.table("waitlist")
        .update({"status": "CONVERTED"})
        .eq("id", clean_id)
        .execute()
    )

    if "status" not in booking_data:
        booking_data["status"] = "CONFIRMED"
    booking_data["waitlist_id"] = clean_id

    return booking_data


class WaitlistService:
    """Service encapsulating waitlist lifecycle, priority calculations, and seat promotions."""

    def __init__(self, supabase_client: Optional[Any] = None) -> None:
        self.supabase = supabase_client

    async def join_waitlist(self, payload: WaitlistJoinRequest) -> dict[str, Any]:
        return await join_waitlist_service(supabase=self.supabase, payload=payload)

    async def get_waitlist_position(self, flight_id: str, email: str) -> dict[str, Any]:
        return await get_waitlist_position_service(
            supabase=self.supabase, flight_id=flight_id, email=email
        )

    async def leave_waitlist(self, waitlist_id: str) -> dict[str, Any]:
        return await leave_waitlist_service(supabase=self.supabase, waitlist_id=waitlist_id)

    async def claim_waitlist_seat(
        self, waitlist_id: str, client_ip: str = "127.0.0.1"
    ) -> dict[str, Any]:
        return await claim_waitlist_seat_service(
            supabase=self.supabase, waitlist_id=waitlist_id, client_ip=client_ip
        )
