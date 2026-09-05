"""Flight domain service for managing flight creation, class allocations, seat maps, and inventory."""

from datetime import datetime, time, timezone
import logging
from typing import Any, Optional
from uuid import UUID
from fastapi import HTTPException, status
from app.models.requests import FlightCreateRequest, SeatMapCreateRequest

logger = logging.getLogger("app.services.flights")


def generate_class_seats(
    start_row: int,
    count: int,
    seat_class: str,
    flight_id: str,
    letters: tuple[str, ...] = ("A", "B", "C", "D", "E", "F"),
) -> list[dict[str, Any]]:
    """Generate physical seat layouts with sequential row numbers and seat letters."""
    seats: list[dict[str, Any]] = []
    current_row = start_row
    while len(seats) < count:
        for letter in letters:
            if len(seats) >= count:
                break
            seats.append(
                {
                    "flight_id": str(flight_id),
                    "seat_number": f"{current_row}{letter}",
                    "seat_class": seat_class,
                    "status": "AVAILABLE",
                }
            )
        current_row += 1
    return seats


async def create_flight_service(
    supabase: Any, payload: FlightCreateRequest, admin_id: str
) -> dict[str, Any]:
    """Register a new scheduled flight route, establish class capacities, and generate seat maps."""
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database client is not available",
        )

    # 1. Duplicate flight protection on UTC calendar date:
    # flight_number + origin_airport + departure_time::date
    dep_dt = payload.departure_time
    if dep_dt.tzinfo is None:
        dep_date = dep_dt.date()
        start_dt = datetime.combine(dep_date, time.min).replace(tzinfo=timezone.utc)
        end_dt = datetime.combine(dep_date, time.max).replace(tzinfo=timezone.utc)
    else:
        dep_utc = dep_dt.astimezone(timezone.utc)
        dep_date = dep_utc.date()
        start_dt = datetime.combine(dep_date, time.min).replace(tzinfo=timezone.utc)
        end_dt = datetime.combine(dep_date, time.max).replace(tzinfo=timezone.utc)

    try:
        existing = (
            await supabase.table("flights")
            .select("id")
            .eq("flight_number", payload.flight_number)
            .eq("origin_airport", payload.origin_airport)
            .gte("departure_time", start_dt.isoformat())
            .lte("departure_time", end_dt.isoformat())
            .execute()
        )
    except Exception as query_exc:
        logger.error("Error checking existing flights: %s", query_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed while checking flight duplication: {str(query_exc)}",
        )

    if existing and existing.data and len(existing.data) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Flight {payload.flight_number} departing from {payload.origin_airport} "
                f"is already scheduled on {dep_date}"
            ),
        )

    # 2. Insert into flights table
    flight_insert_payload = {
        "flight_number": payload.flight_number,
        "origin_airport": payload.origin_airport,
        "destination_airport": payload.destination_airport,
        "departure_time": payload.departure_time.isoformat(),
        "arrival_time": payload.arrival_time.isoformat(),
        "origin_tz": payload.origin_tz,
        "status": "SCHEDULED",
        "total_capacity": payload.total_capacity,
        "schedule_version": 1,
    }

    try:
        flight_res = (
            await supabase.table("flights")
            .insert(flight_insert_payload)
            .execute()
        )
    except Exception as insert_exc:
        err_msg = str(insert_exc)
        if "unique" in err_msg.lower() or "conflict" in err_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Flight {payload.flight_number} schedule conflicts with existing record: {err_msg}",
            )
        logger.error("Error inserting flight: %s", insert_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create flight record: {err_msg}",
        )

    if not flight_res or not flight_res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve created flight record",
        )

    flight = flight_res.data[0]
    flight_id = flight["id"]

    # 3. Insert into flight_classes for FIRST, BUSINESS, ECONOMY
    classes_payload = [
        {
            "flight_id": str(flight_id),
            "seat_class": "FIRST",
            "capacity": payload.first_class_seats,
            "booked_seats": 0,
            "base_price_cents": 250000,
            "overbooking_pct": 0,
        },
        {
            "flight_id": str(flight_id),
            "seat_class": "BUSINESS",
            "capacity": payload.business_class_seats,
            "booked_seats": 0,
            "base_price_cents": 120000,
            "overbooking_pct": 0,
        },
        {
            "flight_id": str(flight_id),
            "seat_class": "ECONOMY",
            "capacity": payload.economy_class_seats,
            "booked_seats": 0,
            "base_price_cents": 45000,
            "overbooking_pct": 5,
        },
    ]

    try:
        classes_res = (
            await supabase.table("flight_classes")
            .insert(classes_payload)
            .execute()
        )
    except Exception as class_exc:
        logger.error("Error inserting flight_classes for flight %s: %s", flight_id, class_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize flight class quotas: {str(class_exc)}",
        )

    # 4. Generate physical seat rows in flight_seats
    # - First Class: 1A..4F (matches first_class_seats)
    # - Business Class: 10A..15F (matches business_class_seats)
    # - Economy Class: 20A..35F (matches economy_class_seats)
    first_seats = generate_class_seats(
        start_row=1,
        count=payload.first_class_seats,
        seat_class="FIRST",
        flight_id=str(flight_id),
    )
    business_seats = generate_class_seats(
        start_row=10,
        count=payload.business_class_seats,
        seat_class="BUSINESS",
        flight_id=str(flight_id),
    )
    economy_seats = generate_class_seats(
        start_row=20,
        count=payload.economy_class_seats,
        seat_class="ECONOMY",
        flight_id=str(flight_id),
    )

    all_seats = first_seats + business_seats + economy_seats
    if all_seats:
        batch_size = 100
        for i in range(0, len(all_seats), batch_size):
            chunk = all_seats[i : i + batch_size]
            try:
                await supabase.table("flight_seats").insert(chunk).execute()
            except Exception as seat_exc:
                logger.error("Error inserting batch of flight_seats: %s", seat_exc)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to generate physical seat map: {str(seat_exc)}",
                )

    # 5. Insert record into admin_audit_logs
    audit_data = {
        "admin_user_id": str(admin_id),
        "action_type": "FLIGHT_CREATED",
        "resource_id": str(flight_id),
        "previous_state": {},
        "new_state": payload.model_dump(mode="json"),
        "ip_address": "127.0.0.1",
        "details": {
            "flight_number": payload.flight_number,
            "origin_airport": payload.origin_airport,
            "destination_airport": payload.destination_airport,
            "total_capacity": payload.total_capacity,
            "seats_generated": len(all_seats),
        },
    }
    try:
        await supabase.table("admin_audit_logs").insert(audit_data).execute()
    except Exception as audit_exc:
        logger.warning("Could not write to admin_audit_logs: %s", audit_exc)

    return {
        **flight,
        "flight_id": flight["id"],
        "classes": classes_res.data if classes_res and classes_res.data else classes_payload,
        "seats_generated": len(all_seats),
    }


async def adjust_capacity_service(
    supabase: Any,
    flight_id: UUID | str,
    seat_class: str,
    new_capacity: int,
    admin_id: str,
) -> dict[str, Any]:
    """Adjust cabin class seat quota, verifying downward guard against active booked seats."""
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database client is not available",
        )

    flight_id_str = str(flight_id)
    seat_class_upper = seat_class.strip().upper()

    if seat_class_upper not in {"FIRST", "BUSINESS", "ECONOMY"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid seat class '{seat_class}'. Must be one of FIRST, BUSINESS, ECONOMY",
        )

    if new_capacity < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Capacity cannot be negative",
        )

    # 1. Fetch existing flight_classes record
    try:
        class_res = (
            await supabase.table("flight_classes")
            .select("*")
            .eq("flight_id", flight_id_str)
            .eq("seat_class", seat_class_upper)
            .maybe_single()
            .execute()
        )
    except Exception as fetch_exc:
        logger.error("Error fetching flight class: %s", fetch_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed while fetching flight class: {str(fetch_exc)}",
        )

    if not class_res or not class_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Flight class '{seat_class_upper}' not found for flight {flight_id_str}",
        )

    existing_class = class_res.data
    booked_seats = int(existing_class.get("booked_seats") or 0)

    # 2. Guard: new_capacity >= booked_seats (cannot shrink below booked count)
    if new_capacity < booked_seats:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot reduce {seat_class_upper} capacity to {new_capacity}. "
                f"Currently booked seats count is {booked_seats}."
            ),
        )

    # 3. Update capacity in flight_classes
    prev_state = dict(existing_class)
    try:
        await (
            supabase.table("flight_classes")
            .update({"capacity": new_capacity})
            .eq("id", existing_class["id"])
            .execute()
        )
    except Exception as update_exc:
        logger.error("Error updating flight_classes capacity: %s", update_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update class capacity: {str(update_exc)}",
        )

    # 4. Recalculate total_capacity across all classes and update flights table
    try:
        all_classes_res = (
            await supabase.table("flight_classes")
            .select("capacity")
            .eq("flight_id", flight_id_str)
            .execute()
        )
    except Exception as sum_exc:
        logger.error("Error reading class capacities: %s", sum_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve class quotas for capacity recalculation: {str(sum_exc)}",
        )

    new_total_capacity = sum(
        int(c.get("capacity") or 0) for c in (all_classes_res.data or [])
    )

    try:
        flight_update_res = (
            await supabase.table("flights")
            .update({"total_capacity": new_total_capacity})
            .eq("id", flight_id_str)
            .execute()
        )
    except Exception as flight_exc:
        logger.error("Error updating flights total_capacity: %s", flight_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update total flight capacity: {str(flight_exc)}",
        )

    if not flight_update_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Flight {flight_id_str} not found while updating total capacity",
        )

    # 5. Write to admin_audit_logs
    audit_data = {
        "admin_user_id": str(admin_id),
        "action_type": "CAPACITY_ADJUSTED",
        "resource_id": flight_id_str,
        "previous_state": prev_state,
        "new_state": {
            "seat_class": seat_class_upper,
            "new_capacity": new_capacity,
            "new_total_capacity": new_total_capacity,
        },
        "ip_address": "127.0.0.1",
        "details": {
            "flight_id": flight_id_str,
            "seat_class": seat_class_upper,
            "previous_capacity": existing_class.get("capacity"),
            "new_capacity": new_capacity,
            "booked_seats": booked_seats,
            "new_total_capacity": new_total_capacity,
        },
    }
    try:
        await supabase.table("admin_audit_logs").insert(audit_data).execute()
    except Exception as audit_exc:
        logger.warning("Could not write capacity adjustment to admin_audit_logs: %s", audit_exc)

    return {
        "flight_id": flight_id_str,
        "seat_class": seat_class_upper,
        "capacity": new_capacity,
        "booked_seats": booked_seats,
        "total_capacity": new_total_capacity,
        "message": f"Successfully updated {seat_class_upper} capacity to {new_capacity}",
    }


async def list_flights_admin_service(supabase: Any) -> list[dict[str, Any]]:
    """Retrieve all flights with class capacity and booked seats breakdown."""
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database client is not available",
        )

    try:
        flights_res = (
            await supabase.table("flights")
            .select("*")
            .order("departure_time", desc=False)
            .execute()
        )
    except Exception as fetch_exc:
        logger.error("Error retrieving flights: %s", fetch_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed while fetching flights: {str(fetch_exc)}",
        )

    flights = flights_res.data or []
    if not flights:
        return []

    try:
        classes_res = await supabase.table("flight_classes").select("*").execute()
    except Exception as class_exc:
        logger.error("Error retrieving flight_classes: %s", class_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed while fetching flight classes: {str(class_exc)}",
        )

    classes_by_flight: dict[str, list[dict[str, Any]]] = {}
    for cls_row in classes_res.data or []:
        fid = str(cls_row.get("flight_id"))
        classes_by_flight.setdefault(fid, []).append(cls_row)

    results: list[dict[str, Any]] = []
    for f in flights:
        fid = str(f.get("id"))
        f_classes = classes_by_flight.get(fid, [])
        total_booked = sum(int(c.get("booked_seats") or 0) for c in f_classes)
        total_cap = int(
            f.get("total_capacity")
            or sum(int(c.get("capacity") or 0) for c in f_classes)
        )
        results.append(
            {
                **f,
                "flight_id": f.get("id"),
                "classes": f_classes,
                "booked_seats": total_booked,
                "total_booked_seats": total_booked,
                "available_seats": max(0, total_cap - total_booked),
            }
        )
    return results


async def get_flight_admin_service(
    supabase: Any, flight_id: UUID | str
) -> dict[str, Any]:
    """Retrieve a single flight with full physical seat map layout and inventory metrics."""
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database client is not available",
        )

    flight_id_str = str(flight_id)
    try:
        flight_res = (
            await supabase.table("flights")
            .select("*")
            .eq("id", flight_id_str)
            .maybe_single()
            .execute()
        )
    except Exception as fetch_exc:
        logger.error("Error retrieving flight details: %s", fetch_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed while fetching flight: {str(fetch_exc)}",
        )

    if not flight_res or not flight_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Flight {flight_id_str} not found",
        )

    flight = flight_res.data

    try:
        classes_res = (
            await supabase.table("flight_classes")
            .select("*")
            .eq("flight_id", flight_id_str)
            .execute()
        )
    except Exception as class_exc:
        logger.error("Error retrieving flight classes: %s", class_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed while fetching flight classes: {str(class_exc)}",
        )

    classes = classes_res.data or []

    try:
        seats_res = (
            await supabase.table("flight_seats")
            .select("id, seat_number, seat_class, status, booking_id")
            .eq("flight_id", flight_id_str)
            .order("seat_number")
            .execute()
        )
    except Exception as seat_exc:
        logger.error("Error retrieving physical seat map: %s", seat_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed while fetching seat layout: {str(seat_exc)}",
        )

    seats = seats_res.data or []

    total_capacity = int(flight.get("total_capacity") or 0)
    total_booked = sum(int(c.get("booked_seats") or 0) for c in classes)
    total_held = sum(1 for s in seats if s.get("status") == "HELD")
    available_seats = max(0, total_capacity - total_booked - total_held)
    occupancy_rate = (
        round((total_booked / total_capacity * 100.0), 2)
        if total_capacity > 0
        else 0.0
    )

    class_breakdown = []
    for c in classes:
        cap = int(c.get("capacity") or 0)
        bk = int(c.get("booked_seats") or 0)
        class_breakdown.append(
            {
                "seat_class": c.get("seat_class"),
                "capacity": cap,
                "booked_seats": bk,
                "available_seats": max(0, cap - bk),
                "base_price_cents": c.get("base_price_cents", 0),
                "overbooking_pct": c.get("overbooking_pct", 0),
            }
        )

    return {
        **flight,
        "flight_id": flight.get("id"),
        "classes": classes,
        "seats": seats,
        "inventory_metrics": {
            "total_capacity": total_capacity,
            "total_booked_seats": total_booked,
            "total_held_seats": total_held,
            "available_seats": available_seats,
            "occupancy_rate_pct": occupancy_rate,
            "class_breakdown": class_breakdown,
        },
    }


async def create_seat_map_service(
    supabase: Any,
    payload: SeatMapCreateRequest,
    admin_id: str,
) -> dict[str, Any]:
    """Bulk configure or overwrite physical seat layout for a flight."""
    if supabase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database client is not available",
        )

    flight_id_str = str(payload.flight_id)

    # 1. Verify flight exists
    flight_res = (
        await supabase.table("flights")
        .select("id, total_capacity")
        .eq("id", flight_id_str)
        .maybe_single()
        .execute()
    )
    if not flight_res or not flight_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Flight {flight_id_str} not found",
        )

    # 2. Check class capacity constraints
    classes_res = (
        await supabase.table("flight_classes")
        .select("seat_class, capacity")
        .eq("flight_id", flight_id_str)
        .execute()
    )
    classes_map = {
        c["seat_class"]: int(c.get("capacity") or 0)
        for c in (classes_res.data or [])
    }

    counts_by_class: dict[str, int] = {}
    for seat in payload.seats:
        cls_name = (
            seat.seat_class.value
            if hasattr(seat.seat_class, "value")
            else str(seat.seat_class)
        )
        counts_by_class[cls_name] = counts_by_class.get(cls_name, 0) + 1

    for cls_name, seat_count in counts_by_class.items():
        declared_cap = classes_map.get(cls_name)
        if declared_cap is not None and seat_count != declared_cap:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Physical seat count for {cls_name} ({seat_count}) does not match "
                    f"allocated class capacity ({declared_cap})"
                ),
            )

    # 3. Overwrite existing unbooked seats
    await (
        supabase.table("flight_seats")
        .delete()
        .eq("flight_id", flight_id_str)
        .eq("status", "AVAILABLE")
        .execute()
    )

    seat_records = [
        {
            "flight_id": flight_id_str,
            "seat_number": s.seat_number,
            "seat_class": (
                s.seat_class.value
                if hasattr(s.seat_class, "value")
                else str(s.seat_class)
            ),
            "status": "AVAILABLE",
        }
        for s in payload.seats
    ]

    batch_size = 100
    for i in range(0, len(seat_records), batch_size):
        chunk = seat_records[i : i + batch_size]
        await supabase.table("flight_seats").insert(chunk).execute()

    # 4. Audit log
    audit_data = {
        "admin_user_id": str(admin_id),
        "action_type": "SEAT_MAP_CREATED",
        "resource_id": flight_id_str,
        "previous_state": {},
        "new_state": {"total_seats": len(seat_records)},
        "ip_address": "127.0.0.1",
        "details": {"flight_id": flight_id_str, "seat_counts": counts_by_class},
    }
    try:
        await supabase.table("admin_audit_logs").insert(audit_data).execute()
    except Exception as audit_exc:
        logger.warning("Could not write seat map creation to admin_audit_logs: %s", audit_exc)

    return {
        "flight_id": flight_id_str,
        "seats_configured": len(seat_records),
        "class_breakdown": counts_by_class,
    }


class FlightService:
    """Service encapsulating flight inventory and scheduling operations for dependency injection."""

    def __init__(self, supabase_client: Optional[Any] = None) -> None:
        self.supabase = supabase_client

    async def get_flight_by_id(self, flight_id: str) -> Optional[dict[str, Any]]:
        """Retrieve flight details by unique identifier."""
        if self.supabase is None:
            return None
        response = (
            await self.supabase.table("flights")
            .select("*")
            .eq("id", flight_id)
            .maybe_single()
            .execute()
        )
        return response.data if response else None

    async def list_active_flights(
        self, origin: Optional[str] = None, destination: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """List active flights with optional origin and destination filters."""
        if self.supabase is None:
            return []
        query = self.supabase.table("flights").select("*").eq("status", "SCHEDULED")
        if origin:
            query = query.eq("origin_airport", origin)
        if destination:
            query = query.eq("destination_airport", destination)
        response = await query.execute()
        return response.data if response and response.data else []

