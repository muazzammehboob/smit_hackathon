"""Administrative endpoints for flight operations and inventory management."""

from typing import Any, Optional
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, status, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from supabase import AsyncClient
from app.dependencies import get_supabase_client, verify_admin_role
from app.models.requests import (
    FlightCreateRequest,
    FlightScheduleUpdateRequest,
    SeatMapCreateRequest,
)
from app.services.flights import (
    adjust_capacity_service,
    create_flight_service,
    create_seat_map_service,
    get_flight_admin_service,
    list_flights_admin_service,
)
from app.utils.audit import log_admin_action
from app.middleware.rbac import require_role

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin_role)],
)


class CapacityAdjustPayload(BaseModel):
    """Payload for adjusting class seat quota."""

    seat_class: str = Field(
        ...,
        min_length=3,
        max_length=20,
        description="Cabin seating class (FIRST, BUSINESS, ECONOMY)",
    )
    new_capacity: int = Field(
        ..., gt=0, description="Target seat capacity for the specified class"
    )

    @field_validator("seat_class", mode="before")
    @classmethod
    def normalize_seat_class(cls, v: Any) -> str:
        """Strip and uppercase seat class string."""
        if isinstance(v, str):
            val = v.strip().upper()
            if val not in {"FIRST", "BUSINESS", "ECONOMY"}:
                raise ValueError("seat_class must be one of FIRST, BUSINESS, ECONOMY")
            return val
        return v


@router.get("/")
async def get_admin_status() -> dict[str, str]:
    """Check admin router readiness."""
    return {"status": "operational", "module": "admin"}


@router.post(
    "/flights",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new scheduled flight route and inventory",
)
async def create_flight(
    payload: FlightCreateRequest,
    admin_user: dict[str, Any] = Depends(verify_admin_role),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> dict[str, Any]:
    """Create a new scheduled flight with class allocations and auto-generated seat map."""
    admin_id = admin_user.get("id") or "system"
    return await create_flight_service(
        supabase=supabase, payload=payload, admin_id=admin_id
    )


@router.get(
    "/flights",
    status_code=status.HTTP_200_OK,
    summary="List all scheduled flights with class capacity and booked seats",
)
async def list_flights(
    admin_user: dict[str, Any] = Depends(verify_admin_role),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> list[dict[str, Any]]:
    """Retrieve all flights with class quota breakdown and booked seat metrics."""
    return await list_flights_admin_service(supabase=supabase)


@router.get(
    "/flights/{id}",
    status_code=status.HTTP_200_OK,
    summary="Get single flight details with complete seat map and inventory metrics",
)
async def get_flight(
    id: UUID,
    admin_user: dict[str, Any] = Depends(verify_admin_role),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> dict[str, Any]:
    """Retrieve details for a single flight including complete physical seat map."""
    return await get_flight_admin_service(supabase=supabase, flight_id=id)


@router.patch(
    "/flights/{id}/capacity",
    status_code=status.HTTP_200_OK,
    summary="Adjust capacity for a specific flight cabin class",
)
async def adjust_capacity(
    id: UUID,
    payload: CapacityAdjustPayload,
    admin_user: dict[str, Any] = Depends(verify_admin_role),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> dict[str, Any]:
    """Adjust cabin class seat capacity with downward guard against active booked seats."""
    admin_id = admin_user.get("id") or "system"
    return await adjust_capacity_service(
        supabase=supabase,
        flight_id=id,
        seat_class=payload.seat_class,
        new_capacity=payload.new_capacity,
        admin_id=admin_id,
    )


@router.post(
    "/flights/{id}/seat-map",
    status_code=status.HTTP_200_OK,
    summary="Configure or overwrite physical seat layout for a flight",
)
async def create_seat_map(
    id: UUID,
    payload: SeatMapCreateRequest,
    admin_user: dict[str, Any] = Depends(verify_admin_role),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> dict[str, Any]:
    """Assign physical seat coordinates to flight classes."""
    payload.flight_id = id
    admin_id = admin_user.get("id") or "system"
    return await create_seat_map_service(
        supabase=supabase, payload=payload, admin_id=admin_id
    )


@router.patch(
    "/flights/{id}/schedule",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_role(["SUPER_ADMIN"]))],
    summary="Update flight schedule",
)
async def update_flight_schedule(
    id: UUID,
    payload: FlightScheduleUpdateRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(verify_admin_role),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> dict[str, Any]:
    # Fetches current flight from flights table
    flight_res = await supabase.table("flights").select("*").eq("id", str(id)).execute()
    if not flight_res.data:
        raise HTTPException(status_code=404, detail="Flight not found")
    flight = flight_res.data[0]
    
    old_departure = datetime.fromisoformat(flight["departure_time"].replace("Z", "+00:00"))
    new_departure = payload.departure_time
    # handle tzinfo if needed, assume they are comparable or both naive/aware
    if old_departure.tzinfo and not new_departure.tzinfo:
        new_departure = new_departure.replace(tzinfo=old_departure.tzinfo)
    elif not old_departure.tzinfo and new_departure.tzinfo:
        old_departure = old_departure.replace(tzinfo=new_departure.tzinfo)
        
    shift_minutes = abs((new_departure - old_departure).total_seconds()) / 60.0
    schedule_disrupted = shift_minutes > 180

    update_data = {
        "departure_time": payload.departure_time.isoformat(),
        "arrival_time": payload.arrival_time.isoformat(),
        "schedule_version": flight.get("schedule_version", 0) + 1
    }
    if schedule_disrupted and "schedule_disrupted" in flight:
        update_data["schedule_disrupted"] = True

    updated_res = await supabase.table("flights").update(update_data).eq("id", str(id)).execute()
    updated_flight = updated_res.data[0]
    
    if schedule_disrupted:
        await supabase.table("bookings").update({"schedule_disrupted": True}).eq("flight_id", str(id)).eq("status", "CONFIRMED").execute()
    
    admin_id = admin_user.get("id") or admin_user.get("email", "system")
    
    await log_admin_action(
        supabase=supabase,
        admin_id=admin_id,
        action_type="SCHEDULE_UPDATE",
        resource_id=str(id),
        previous_state=flight,
        new_state=updated_flight,
        details={"shift_minutes": shift_minutes, "schedule_disrupted": schedule_disrupted}
    )
    
    return {
        "flight": updated_flight,
        "shift_minutes": shift_minutes,
        "schedule_disrupted": schedule_disrupted
    }


@router.post(
    "/flights/{id}/cancel",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_role(["SUPER_ADMIN"]))],
    summary="Cancel a flight",
)
async def cancel_flight(
    id: UUID,
    request: Request,
    admin_user: dict[str, Any] = Depends(verify_admin_role),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> dict[str, Any]:
    flight_res = await supabase.table("flights").select("*").eq("id", str(id)).execute()
    if not flight_res.data:
        raise HTTPException(status_code=404, detail="Flight not found")
    flight = flight_res.data[0]
    
    await supabase.table("flights").update({"status": "CANCELLED"}).eq("id", str(id)).execute()
    await supabase.table("seat_holds").update({"status": "EXPIRED"}).eq("flight_id", str(id)).in_("status", ["HELD"]).execute()
    
    waitlist_res = await supabase.table("waitlist").update({"status": "CANCELLED"}).eq("flight_id", str(id)).eq("status", "WAITING").execute()
    cancelled_waitlist_count = len(waitlist_res.data) if waitlist_res.data else 0
    
    bookings_res = await supabase.table("bookings").update({
        "status": "CANCELLED",
        "schedule_disrupted": True
    }).eq("flight_id", str(id)).eq("status", "CONFIRMED").execute()
    disrupted_bookings_count = len(bookings_res.data) if bookings_res.data else 0
    
    await supabase.table("flight_seats").update({
        "status": "AVAILABLE",
        "booking_id": None
    }).eq("flight_id", str(id)).execute()
    
    await supabase.table("flight_classes").update({"booked_seats": 0}).eq("flight_id", str(id)).execute()
    
    admin_id = admin_user.get("id") or admin_user.get("email", "system")
    await log_admin_action(
        supabase=supabase,
        admin_id=admin_id,
        action_type="FLIGHT_CANCEL",
        resource_id=str(id),
        previous_state=flight,
        new_state={"status": "CANCELLED"}
    )
    
    return {
        "flight_id": str(id),
        "status": "CANCELLED",
        "disrupted_bookings_count": disrupted_bookings_count,
        "cancelled_waitlist_count": cancelled_waitlist_count,
        "message": "Flight cancelled by airline with 100% refund eligibility"
    }


@router.get(
    "/audit-logs",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_role(["SUPER_ADMIN", "OPS_AGENT"]))],
    summary="Get audit logs",
)
async def get_audit_logs(
    limit: int = 50,
    offset: int = 0,
    action_type: Optional[str] = None,
    supabase: AsyncClient = Depends(get_supabase_client),
) -> list[dict[str, Any]]:
    query = supabase.table("admin_audit_logs").select("*").order("created_at", desc=True).limit(limit).offset(offset)
    if action_type:
        query = query.eq("action_type", action_type)
        
    res = await query.execute()
    return res.data or []


