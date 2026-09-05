"""Pydantic data models for request and response schemas."""

from app.models.requests import (
    BaseRequest,
    BookingConfirmRequest,
    CancellationRequest,
    FareClassEnum,
    FlightCapacityUpdateRequest,
    FlightCreateRequest,
    FlightScheduleUpdateRequest,
    FlightSearchRequest,
    LoyaltyTierEnum,
    SeatClassEnum,
    SeatHoldRequest,
    SeatItem,
    SeatMapCreateRequest,
    WaitlistJoinRequest,
)
from app.models.responses import (
    BaseResponse,
    BookingResponse,
    CancellationResponse,
    ErrorResponse,
    FlightResponse,
    FlightSearchResult,
    HealthResponse,
    HoldResponse,
    SeatAvailability,
    SeatDetailResponse,
    SeatMapResponse,
    WaitlistResponse,
)

__all__ = [
    # Requests and Enums
    "BaseRequest",
    "BookingConfirmRequest",
    "CancellationRequest",
    "FareClassEnum",
    "FlightCapacityUpdateRequest",
    "FlightCreateRequest",
    "FlightScheduleUpdateRequest",
    "FlightSearchRequest",
    "LoyaltyTierEnum",
    "SeatClassEnum",
    "SeatHoldRequest",
    "SeatItem",
    "SeatMapCreateRequest",
    "WaitlistJoinRequest",
    # Responses
    "BaseResponse",
    "BookingResponse",
    "CancellationResponse",
    "ErrorResponse",
    "FlightResponse",
    "FlightSearchResult",
    "HealthResponse",
    "HoldResponse",
    "SeatAvailability",
    "SeatDetailResponse",
    "SeatMapResponse",
    "WaitlistResponse",
]

