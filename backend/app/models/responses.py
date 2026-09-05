"""Response schemas and standardized output models for the Flight Management System API."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class BaseResponse(BaseModel):
    """Standard base response model for API endpoints."""

    model_config = ConfigDict(extra="ignore")
    success: bool = True
    message: Optional[str] = None
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    """Standard error response format matching global exception handling."""

    detail: str
    error_code: str


class HealthResponse(BaseModel):
    """Health check response payload."""

    status: str = "healthy"
    service: str = "flight-management-api"


class SeatAvailability(BaseModel):
    """Seat availability and base pricing breakdown per cabin class."""

    seat_class: str
    total_capacity: int = Field(..., ge=0)
    available_seats: int = Field(..., ge=0)
    base_price_cents: int = Field(..., ge=0)


class FlightSearchResult(BaseModel):
    """Single flight search result item with live seat availability and price lock."""

    flight_id: UUID
    flight_number: str
    origin: str
    destination: str
    departure_time: datetime
    arrival_time: datetime
    availability: list[SeatAvailability] = Field(default_factory=list)
    price_lock_token: str
    price_lock_expires_at: datetime


class HoldResponse(BaseModel):
    """Temporary reservation hold confirmation details."""

    hold_id: UUID
    seat_id: UUID
    seat_number: str
    status: str = "HELD"
    expires_at: datetime


class BookingResponse(BaseModel):
    """Confirmed booking ticket confirmation record."""

    booking_id: UUID
    pnr: str
    flight_number: str
    passenger_name: str
    passenger_email: str
    fare_class: str
    fare_paid_cents: int
    seat_number: str
    status: str
    created_at: datetime


class CancellationResponse(BaseModel):
    """Cancellation resolution and financial refund outcome."""

    pnr: str
    refund_amount_cents: int = Field(..., ge=0)
    refund_type: str
    status: str
    seat_released: bool


class WaitlistResponse(BaseModel):
    """Standing waitlist queue registration details."""

    waitlist_id: UUID
    flight_id: UUID
    passenger_name: str
    loyalty_tier: str
    priority_score: int
    status: str


class SeatDetailResponse(BaseModel):
    """Physical seat item in aircraft layout representation."""

    id: UUID
    seat_number: str
    seat_class: str
    status: str


class SeatMapResponse(BaseModel):
    """Complete physical seat map layout for a scheduled flight."""

    flight_id: UUID
    seats: list[SeatDetailResponse] = Field(default_factory=list)


class FlightResponse(BaseModel):
    """Detailed flight entity representation."""

    flight_id: UUID
    flight_number: str
    origin_airport: str
    destination_airport: str
    departure_time: datetime
    arrival_time: datetime
    origin_tz: str
    status: str
    total_capacity: int
    schedule_version: int
    created_at: datetime

