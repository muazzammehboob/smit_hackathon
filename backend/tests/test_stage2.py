"""Comprehensive unit tests for Stage 2A and 2B: Configuration, Dependencies, and Models."""

from datetime import date, datetime, timezone
from uuid import uuid4
import pytest
from fastapi import HTTPException

from app.config import Settings, settings
from app.dependencies import (
    get_current_user,
    get_supabase_client,
    verify_admin_role,
    verify_idempotency_key,
)
from app.models.requests import (
    BookingConfirmRequest,
    CancellationRequest,
    FareClassEnum,
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
    BookingResponse,
    CancellationResponse,
    ErrorResponse,
    FlightResponse,
    FlightSearchResult,
    HoldResponse,
    SeatAvailability,
    WaitlistResponse,
)


# ==============================================================================
# 1. Config Tests (Stage 2A)
# ==============================================================================
def test_config_properties_and_defaults() -> None:
    """Validate that settings correctly exposes uppercase fields and lowercase properties."""
    assert settings.SUPABASE_URL.startswith("http")
    assert settings.supabase_url == settings.SUPABASE_URL
    assert settings.supabase_anon_key != ""
    assert settings.supabase_service_role_key != ""
    assert settings.N8N_WEBHOOK_SECRET == "hackathon-secret"
    assert settings.PRICE_LOCK_SECRET == "price-lock-secret-key-12345"
    assert isinstance(settings.CORS_ORIGINS, list)


def test_cors_origins_parsing() -> None:
    """Ensure CORS origins validator handles list, JSON string, and comma-separated string."""
    parsed_json = Settings.parse_cors_origins('["http://site.com", "http://other.com"]')
    assert parsed_json == ["http://site.com", "http://other.com"]

    parsed_csv = Settings.parse_cors_origins("http://site1.com, http://site2.com")
    assert parsed_csv == ["http://site1.com", "http://site2.com"]

    parsed_list = Settings.parse_cors_origins(["http://site1.com"])
    assert parsed_list == ["http://site1.com"]


# ==============================================================================
# 2. Dependency Tests (Stage 2A)
# ==============================================================================
def test_verify_idempotency_key() -> None:
    """Verify Idempotency-Key header extraction and validation."""
    import asyncio

    async def run() -> None:
        key = await verify_idempotency_key("idem-key-123")
        assert key == "idem-key-123"

        with pytest.raises(HTTPException) as exc_missing:
            await verify_idempotency_key(None)
        assert exc_missing.value.status_code == 400

        with pytest.raises(HTTPException) as exc_blank:
            await verify_idempotency_key("   ")
        assert exc_blank.value.status_code == 400

        with pytest.raises(HTTPException) as exc_long:
            await verify_idempotency_key("x" * 256)
        assert exc_long.value.status_code == 400

    asyncio.run(run())


def test_get_current_user_test_tokens() -> None:
    """Verify built-in test tokens for deterministic testing."""
    import asyncio

    async def run() -> None:
        user = await get_current_user(authorization="Bearer test-token")
        assert user["id"] == "00000000-0000-0000-0000-000000000001"
        assert user["is_admin"] is False

        admin_user = await get_current_user(authorization="Bearer dev-admin-token")
        assert admin_user["is_admin"] is True
        assert admin_user["app_metadata"]["role"] == "SUPER_ADMIN"

        with pytest.raises(HTTPException) as exc_no_auth:
            await get_current_user(authorization=None)
        assert exc_no_auth.value.status_code == 401

    asyncio.run(run())


def test_verify_admin_role_bypass() -> None:
    """Verify X-Admin-Role bypass allows rapid demo access without strict tokens."""
    import asyncio

    async def run() -> None:
        bypassed = await verify_admin_role(x_admin_role="SUPER_ADMIN")
        assert bypassed["is_admin"] is True

        ops_bypassed = await verify_admin_role(x_admin_role="OPS_AGENT")
        assert ops_bypassed["is_admin"] is True

        mock_admin_user = {
            "id": "1",
            "email": "adm@test.com",
            "is_admin": True,
            "app_metadata": {"role": "SUPER_ADMIN"},
        }
        verified = await verify_admin_role(x_admin_role=None, user=mock_admin_user)
        assert verified == mock_admin_user

        mock_regular_user = {
            "id": "2",
            "email": "user@test.com",
            "is_admin": False,
            "app_metadata": {"role": "authenticated"},
        }
        with pytest.raises(HTTPException) as exc_forbidden:
            await verify_admin_role(x_admin_role=None, user=mock_regular_user)
        assert exc_forbidden.value.status_code == 403

    asyncio.run(run())


def test_get_supabase_client_singleton() -> None:
    """Verify singleton pattern returns an active AsyncClient instance."""
    import asyncio

    async def run() -> None:
        client1 = await get_supabase_client()
        client2 = await get_supabase_client()
        assert client1 is client2

    asyncio.run(run())


# ==============================================================================
# 3. Request Model Tests (Stage 2B)
# ==============================================================================
def test_flight_create_request_valid() -> None:
    """Verify valid FlightCreateRequest creation with automatic uppercase normalization."""
    req = FlightCreateRequest(
        flight_number="pk-301",
        origin_airport="khi",
        destination_airport="isb",
        departure_time=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
        arrival_time=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
        total_capacity=100,
        first_class_seats=10,
        business_class_seats=20,
        economy_class_seats=70,
        origin_tz="Asia/Karachi",
    )
    assert req.flight_number == "PK-301"
    assert req.origin_airport == "KHI"
    assert req.destination_airport == "ISB"
    assert req.total_capacity == 100


def test_flight_create_request_invariants() -> None:
    """Verify model validators for distinct airports, arrival sequence, and seat sums."""
    # Invariant: Origin == Destination
    with pytest.raises(ValueError, match="Origin and destination airports must be distinct"):
        FlightCreateRequest(
            flight_number="PK-301",
            origin_airport="DXB",
            destination_airport="DXB",
            departure_time=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
            arrival_time=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
            total_capacity=100,
            first_class_seats=10,
            business_class_seats=20,
            economy_class_seats=70,
        )

    # Invariant: Arrival <= Departure
    with pytest.raises(ValueError, match="Arrival time must be strictly after departure time"):
        FlightCreateRequest(
            flight_number="PK-301",
            origin_airport="LHR",
            destination_airport="JFK",
            departure_time=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
            arrival_time=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
            total_capacity=100,
            first_class_seats=10,
            business_class_seats=20,
            economy_class_seats=70,
        )

    # Invariant: Class capacities sum != total_capacity
    with pytest.raises(ValueError, match="Sum of class seats .* must equal total capacity"):
        FlightCreateRequest(
            flight_number="PK-301",
            origin_airport="LHR",
            destination_airport="DXB",
            departure_time=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
            arrival_time=datetime(2026, 9, 10, 18, 0, tzinfo=timezone.utc),
            total_capacity=100,
            first_class_seats=10,
            business_class_seats=20,
            economy_class_seats=50,  # sum is 80 != 100
        )


def test_seat_map_create_request() -> None:
    """Verify unique seat numbers validation in SeatMapCreateRequest."""
    flight_id = uuid4()
    req = SeatMapCreateRequest(
        flight_id=flight_id,
        seats=[
            SeatItem(seat_number="1A", seat_class=SeatClassEnum.FIRST),
            SeatItem(seat_number="1B", seat_class=SeatClassEnum.FIRST),
            SeatItem(seat_number="10A", seat_class=SeatClassEnum.ECONOMY),
        ],
    )
    assert len(req.seats) == 3

    with pytest.raises(ValueError, match="Duplicate seat number"):
        SeatMapCreateRequest(
            flight_id=flight_id,
            seats=[
                SeatItem(seat_number="1A", seat_class=SeatClassEnum.FIRST),
                SeatItem(seat_number="1a", seat_class=SeatClassEnum.FIRST),
            ],
        )


def test_flight_schedule_update_request() -> None:
    """Verify schedule order validation."""
    req = FlightScheduleUpdateRequest(
        departure_time=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
        arrival_time=datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc),
    )
    assert req.arrival_time > req.departure_time

    with pytest.raises(ValueError, match="Arrival time must be strictly after departure time"):
        FlightScheduleUpdateRequest(
            departure_time=datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc),
            arrival_time=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
        )


def test_flight_search_request() -> None:
    """Verify search parameters and airport normalization."""
    req = FlightSearchRequest(
        origin="dxb",
        destination="lhr",
        date=date(2026, 9, 15),
        seat_class=SeatClassEnum.BUSINESS,
    )
    assert req.origin == "DXB"
    assert req.destination == "LHR"
    assert req.seat_class == SeatClassEnum.BUSINESS

    with pytest.raises(ValueError, match="Origin and destination airports must be distinct"):
        FlightSearchRequest(origin="DXB", destination="DXB", date=date(2026, 9, 15))


def test_seat_hold_request() -> None:
    """Verify SeatHoldRequest constraints."""
    req = SeatHoldRequest(
        flight_id=uuid4(),
        seat_id=uuid4(),
        session_id="sess-abc-123",
        hold_minutes=15,
    )
    assert req.hold_minutes == 15

    with pytest.raises(ValueError):
        SeatHoldRequest(
            flight_id=uuid4(),
            seat_id=uuid4(),
            session_id="sess",
            hold_minutes=0,
        )


def test_booking_confirm_request() -> None:
    """Verify BookingConfirmRequest with valid email, fare, and price lock."""
    req = BookingConfirmRequest(
        hold_id=uuid4(),
        passenger_name="John Doe",
        passenger_email="john.doe@example.com",
        fare_class=FareClassEnum.FLEXIBLE,
        fare_cents=25000,
        price_lock_token="token-xyz-987",
    )
    assert req.fare_cents == 25000
    assert req.passenger_name == "John Doe"

    with pytest.raises(ValueError):
        BookingConfirmRequest(
            hold_id=uuid4(),
            passenger_name="J",  # too short (< 2)
            passenger_email="invalid-email",
            fare_class=FareClassEnum.BASIC_ECONOMY,
            fare_cents=10000,
            price_lock_token="token",
        )


def test_cancellation_and_waitlist_requests() -> None:
    """Verify CancellationRequest and WaitlistJoinRequest schema contracts."""
    p_id = uuid4()
    cancel_req = CancellationRequest(passenger_ids=[p_id], reason="Travel plan change")
    assert cancel_req.passenger_ids == [p_id]

    waitlist_req = WaitlistJoinRequest(
        flight_id=uuid4(),
        requested_class=SeatClassEnum.FIRST,
        passenger_name="Alice Smith",
        passenger_email="alice@example.com",
        loyalty_tier=LoyaltyTierEnum.PLATINUM,
    )
    assert waitlist_req.loyalty_tier == LoyaltyTierEnum.PLATINUM


# ==============================================================================
# 4. Response Model Tests (Stage 2B)
# ==============================================================================
def test_response_models_serialization() -> None:
    """Verify serialization of all key response schemas."""
    flight_id = uuid4()
    hold_id = uuid4()
    seat_id = uuid4()
    booking_id = uuid4()
    now = datetime.now(timezone.utc)

    # 1. SeatAvailability & FlightSearchResult
    avail = SeatAvailability(
        seat_class="ECONOMY",
        total_capacity=100,
        available_seats=45,
        base_price_cents=15000,
    )
    search_res = FlightSearchResult(
        flight_id=flight_id,
        flight_number="PK-301",
        origin="KHI",
        destination="ISB",
        departure_time=now,
        arrival_time=now,
        availability=[avail],
        price_lock_token="lock-123",
        price_lock_expires_at=now,
    )
    assert search_res.flight_number == "PK-301"
    assert search_res.availability[0].available_seats == 45

    # 2. HoldResponse
    hold_res = HoldResponse(
        hold_id=hold_id,
        seat_id=seat_id,
        seat_number="12A",
        status="HELD",
        expires_at=now,
    )
    assert hold_res.seat_number == "12A"

    # 3. BookingResponse
    booking_res = BookingResponse(
        booking_id=booking_id,
        pnr="ABC234",
        flight_number="PK-301",
        passenger_name="John Doe",
        passenger_email="john@example.com",
        fare_class="FLEXIBLE",
        fare_paid_cents=25000,
        seat_number="12A",
        status="CONFIRMED",
        created_at=now,
    )
    assert booking_res.pnr == "ABC234"

    # 4. CancellationResponse
    cancel_res = CancellationResponse(
        pnr="ABC234",
        refund_amount_cents=20000,
        refund_type="CASH",
        status="CANCELLED",
        seat_released=True,
    )
    assert cancel_res.seat_released is True

    # 5. WaitlistResponse
    waitlist_res = WaitlistResponse(
        waitlist_id=uuid4(),
        flight_id=flight_id,
        passenger_name="Alice",
        loyalty_tier="PLATINUM",
        priority_score=950,
        status="WAITING",
    )
    assert waitlist_res.priority_score == 950

    # 6. ErrorResponse
    err_res = ErrorResponse(detail="Seat unavailable", error_code="SEAT_HELD")
    assert err_res.error_code == "SEAT_HELD"

    # 7. FlightResponse
    flight_res = FlightResponse(
        flight_id=flight_id,
        flight_number="PK-301",
        origin_airport="KHI",
        destination_airport="ISB",
        departure_time=now,
        arrival_time=now,
        origin_tz="UTC",
        status="SCHEDULED",
        total_capacity=100,
        schedule_version=1,
        created_at=now,
    )
    assert flight_res.flight_number == "PK-301"
