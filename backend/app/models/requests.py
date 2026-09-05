"""Request schemas and payload definitions for the Flight Management System API."""

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class SeatClassEnum(str, Enum):
    """Supported cabin seating classes."""

    FIRST = "FIRST"
    BUSINESS = "BUSINESS"
    ECONOMY = "ECONOMY"


class FareClassEnum(str, Enum):
    """Ticketing fare tier classifications."""

    BASIC_ECONOMY = "BASIC_ECONOMY"
    FLEXIBLE = "FLEXIBLE"
    PREMIUM_FIRST = "PREMIUM_FIRST"


class LoyaltyTierEnum(str, Enum):
    """Frequent flyer priority loyalty tiers."""

    PLATINUM = "PLATINUM"
    GOLD = "GOLD"
    SILVER = "SILVER"
    GENERAL = "GENERAL"


class BaseRequest(BaseModel):
    """Base request model with strict extra parameter handling."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FlightCreateRequest(BaseRequest):
    """Payload for registering a new scheduled flight route."""

    flight_number: str = Field(..., min_length=3, max_length=10)
    origin_airport: str = Field(..., min_length=3, max_length=3)
    destination_airport: str = Field(..., min_length=3, max_length=3)
    departure_time: datetime
    arrival_time: datetime
    total_capacity: int = Field(..., gt=0)
    first_class_seats: int = Field(default=0, ge=0)
    business_class_seats: int = Field(default=0, ge=0)
    economy_class_seats: int = Field(default=0, ge=0)
    origin_tz: str = Field(default="UTC", min_length=1, max_length=50)

    @field_validator("flight_number", "origin_airport", "destination_airport", mode="before")
    @classmethod
    def normalize_codes(cls, value: Any) -> Any:
        """Strip whitespace and enforce uppercase on flight and airport codes."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def validate_flight_invariants(self) -> "FlightCreateRequest":
        """Verify airport uniqueness, arrival sequence, and exact seat capacity sum."""
        if self.origin_airport == self.destination_airport:
            raise ValueError("Origin and destination airports must be distinct")

        if self.arrival_time <= self.departure_time:
            raise ValueError("Arrival time must be strictly after departure time")

        allocated_sum = (
            self.first_class_seats + self.business_class_seats + self.economy_class_seats
        )
        if allocated_sum != self.total_capacity:
            raise ValueError(
                f"Sum of class seats ({allocated_sum}) must equal total capacity ({self.total_capacity})"
            )

        return self


class SeatItem(BaseRequest):
    """Individual seat configuration item in an aircraft layout."""

    seat_number: str = Field(..., min_length=1, max_length=10)
    seat_class: SeatClassEnum

    @field_validator("seat_number", mode="before")
    @classmethod
    def normalize_seat_number(cls, value: Any) -> Any:
        """Enforce uppercase on seat identification numbers."""
        if isinstance(value, str):
            return value.strip().upper()
        return value


class SeatMapCreateRequest(BaseRequest):
    """Payload for initializing physical aircraft seat layout for a flight."""

    flight_id: UUID
    seats: list[SeatItem] = Field(..., min_length=1)

    @field_validator("seats")
    @classmethod
    def validate_unique_seats(cls, seats: list[SeatItem]) -> list[SeatItem]:
        """Ensure no duplicate seat numbers exist within the batch payload."""
        seen = set()
        for seat in seats:
            normalized = seat.seat_number.strip().upper()
            if normalized in seen:
                raise ValueError(f"Duplicate seat number found in seat map: {seat.seat_number}")
            seen.add(normalized)
        return seats


class FlightScheduleUpdateRequest(BaseRequest):
    """Payload for updating departure and arrival operational timestamps."""

    departure_time: datetime
    arrival_time: datetime

    @model_validator(mode="after")
    def validate_schedule_order(self) -> "FlightScheduleUpdateRequest":
        """Ensure arrival occurs strictly after updated departure timestamp."""
        if self.arrival_time <= self.departure_time:
            raise ValueError("Arrival time must be strictly after departure time")
        return self


class FlightSearchRequest(BaseRequest):
    """Query filter parameters for searching available flights."""

    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    date: date
    seat_class: Optional[SeatClassEnum] = None

    @field_validator("origin", "destination", mode="before")
    @classmethod
    def normalize_search_airports(cls, value: Any) -> Any:
        """Ensure airport search codes are capitalized."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def validate_distinct_airports(self) -> "FlightSearchRequest":
        """Guard against identical origin and destination search queries."""
        if self.origin == self.destination:
            raise ValueError("Origin and destination airports must be distinct")
        return self


class SeatHoldRequest(BaseRequest):
    """Payload for establishing a temporary atomic seat hold reservation."""

    flight_id: UUID
    seat_id: UUID
    session_id: str = Field(..., min_length=1, max_length=100)
    hold_minutes: int = Field(default=10, ge=1, le=120)


class BookingConfirmRequest(BaseRequest):
    """Payload for finalizing and converting a held seat into a confirmed booking."""

    hold_id: UUID
    passenger_name: str = Field(..., min_length=2, max_length=150)
    passenger_email: EmailStr
    fare_class: FareClassEnum
    fare_cents: int = Field(..., gt=0)
    price_lock_token: str = Field(..., min_length=1)


class CancellationRequest(BaseRequest):
    """Payload for canceling full booking or specific passenger tickets."""

    passenger_ids: Optional[list[UUID]] = None
    reason: Optional[str] = None


class WaitlistJoinRequest(BaseRequest):
    """Payload for entering the priority standby waitlist on a full flight."""

    flight_id: UUID
    requested_class: SeatClassEnum
    passenger_name: str = Field(..., min_length=2, max_length=150)
    passenger_email: EmailStr
    loyalty_tier: LoyaltyTierEnum = LoyaltyTierEnum.GENERAL


class FlightCapacityUpdateRequest(BaseRequest):
    """Payload for administrative adjustment of class seat quotas."""

    total_capacity: Optional[int] = Field(default=None, gt=0)
    first_class_seats: Optional[int] = Field(default=None, ge=0)
    business_class_seats: Optional[int] = Field(default=None, ge=0)
    economy_class_seats: Optional[int] = Field(default=None, ge=0)

