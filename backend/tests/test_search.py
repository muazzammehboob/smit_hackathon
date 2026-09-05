"""Comprehensive unit and integration tests for Stage 2D: Search Routes and Fare Rules."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
from uuid import UUID, uuid4
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.dependencies import close_supabase_client
from app.main import app
from app.routes.search import (
    FARE_RULES,
    generate_price_lock_token,
    verify_price_lock_token,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    await close_supabase_client()


def test_generate_price_lock_token_hmac():
    flight_id = str(uuid4())
    seat_class = "ECONOMY"
    price_cents = 45000

    token, expires_at = generate_price_lock_token(flight_id, seat_class, price_cents)

    assert isinstance(token, str)
    assert len(token) == 64
    assert isinstance(expires_at, datetime)

    now_utc = datetime.now(timezone.utc)
    diff_seconds = (expires_at - now_utc).total_seconds()
    assert 890 <= diff_seconds <= 910

    ts = int(expires_at.timestamp())
    expected_msg = f"{flight_id}:{seat_class}:{price_cents}:{ts}"
    expected_sig = hmac.new(
        settings.PRICE_LOCK_SECRET.encode('utf-8'),
        expected_msg.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()

    assert token == expected_sig


def test_verify_price_lock_token_tamper_detection():
    flight_id = str(uuid4())
    seat_class = "BUSINESS"
    price_cents = 120000

    token, expires_at = generate_price_lock_token(flight_id, seat_class, price_cents)

    assert verify_price_lock_token(token, flight_id, seat_class, price_cents, expires_at) is True
    assert verify_price_lock_token(token, flight_id, seat_class, price_cents + 1000, expires_at) is False
    assert verify_price_lock_token(token, flight_id, "ECONOMY", price_cents, expires_at) is False
    assert verify_price_lock_token(token, str(uuid4()), seat_class, price_cents, expires_at) is False

    past_expiry = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert verify_price_lock_token(token, flight_id, seat_class, price_cents, past_expiry) is False


@pytest.mark.anyio
async def test_fare_rules_endpoint(client):
    response = await client.get("/api/v1/flights/fare-rules")
    assert response.status_code == 200
    data = response.json()

    assert "BASIC_ECONOMY" in data
    assert "FLEXIBLE" in data
    assert "PREMIUM_FIRST" in data

    basic = data["BASIC_ECONOMY"]
    assert basic["refund_pct"] == 0

    flex = data["FLEXIBLE"]
    assert flex["refund_pct"] == 90

    first = data["PREMIUM_FIRST"]
    assert first["refund_pct"] == 100


@pytest.mark.anyio
async def test_search_validation_identical_airports(client):
    response = await client.get(
        "/api/v1/flights/search",
        params={"origin": "LHR", "destination": "LHR", "date": "2026-09-08"},
    )
    assert response.status_code == 400
    assert "distinct" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_search_validation_invalid_class(client):
    response = await client.get(
        "/api/v1/flights/search",
        params={
            "origin": "LHR",
            "destination": "DXB",
            "date": "2026-09-08",
            "seat_class": "SUPER_VIP",
        },
    )
    assert response.status_code == 400
    assert "invalid seat class" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_search_existing_flight_and_inventory(client):
    response = await client.get(
        "/api/v1/flights/search",
        params={"origin": "LHR", "destination": "DXB", "date": "2026-09-08"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1

    flight = data[0]
    assert flight["flight_number"] == "BA101"
    assert flight["origin"] == "LHR"
    assert flight["destination"] == "DXB"
    assert len(flight["price_lock_token"]) == 64
    assert flight["price_lock_expires_at"] is not None

    avail_classes = {item["seat_class"]: item for item in flight["availability"]}
    assert "FIRST" in avail_classes
    assert "BUSINESS" in avail_classes
    assert "ECONOMY" in avail_classes

    assert avail_classes["FIRST"]["available_seats"] >= 0
    assert avail_classes["ECONOMY"]["available_seats"] >= 0


@pytest.mark.anyio
async def test_search_with_seat_class_filter(client):
    response = await client.get(
        "/api/v1/flights/search",
        params={
            "origin": "LHR",
            "destination": "DXB",
            "date": "2026-09-08",
            "seat_class": "ECONOMY",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1

    for flight in data:
        assert len(flight["availability"]) == 1
        assert flight["availability"][0]["seat_class"] == "ECONOMY"


@pytest.mark.anyio
async def test_search_with_class_alias_filter(client):
    response = await client.get(
        "/api/v1/flights/search",
        params={
            "origin": "LHR",
            "destination": "DXB",
            "date": "2026-09-08",
            "class": "BUSINESS",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1

    for flight in data:
        assert len(flight["availability"]) == 1
        assert flight["availability"][0]["seat_class"] == "BUSINESS"


@pytest.mark.anyio
async def test_search_no_matching_flights(client):
    response = await client.get(
        "/api/v1/flights/search",
        params={"origin": "JFK", "destination": "DXB", "date": "2030-01-01"},
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_get_flight_seats_expired_hold_displayed_as_available(client):
    flight_id = "11111111-1111-1111-1111-111111111111"
    response = await client.get(f"/api/v1/flights/{flight_id}/seats")
    assert response.status_code == 200
    data = response.json()

    assert data["flight_id"] == flight_id
    seats = data["seats"]
    assert len(seats) == 100

    seat_20b = next((s for s in seats if s["seat_number"] == "20B"), None)
    assert seat_20b is not None
    assert seat_20b["status"] == "AVAILABLE"

    valid_statuses = {"AVAILABLE", "HELD", "BOOKED", "BLOCKED"}
    for seat in seats:
        assert seat["status"] in valid_statuses
        assert seat["seat_class"] in {"FIRST", "BUSINESS", "ECONOMY"}


@pytest.mark.anyio
async def test_get_flight_seats_not_found(client):
    missing_id = str(uuid4())
    response = await client.get(f"/api/v1/flights/{missing_id}/seats")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_search_router_readiness(client):
    response = await client.get("/api/v1/flights/")
    assert response.status_code == 200
    assert response.json() == {"status": "operational", "module": "search"}

