"""Response schemas and standardized output models for the Flight Management System API."""

from datetime import datetime
from typing import Any, Optional, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class PartialCancellationResponse(BaseModel):
    """Partial booking cancellation resolution for specified passengers."""

    status: str
    child_pnr: Optional[str] = None
    original_pnr: Optional[str] = None
    cancelled_passengers: list[Any] = Field(default_factory=list)
    refund_amount_cents: int = Field(default=0, ge=0)
    refund_type: Optional[str] = None
    remaining_passengers: Optional[int] = None


class TravelCreditResponse(BaseModel):
    """Travel credit voucher issuance response."""

    status: str
    voucher_code: Optional[str] = None
    credit_code: Optional[str] = None
    amount_cents: int = Field(default=0, ge=0)
    balance_cents: int = Field(default=0, ge=0)
    expires_at: Union[str, datetime]
    pnr: Optional[str] = None
    booking_id: Optional[Union[str, UUID]] = None
    passenger_email: Optional[str] = None

    @model_validator(mode="after")
    def sync_credit_fields(self) -> "TravelCreditResponse":
        """Ensure voucher_code and credit_code are synchronized."""
        if self.voucher_code and not self.credit_code:
            self.credit_code = self.voucher_code
        elif self.credit_code and not self.voucher_code:
            self.voucher_code = self.credit_code
        return self


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

