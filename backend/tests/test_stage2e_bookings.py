"""Comprehensive unit and integration tests for Stage 2E: Atomic Seat Hold and Idempotent Booking Confirmation."""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_supabase_client
from app.models.requests import BookingConfirmRequest, FareClassEnum, SeatHoldRequest
from app.models.responses import BookingResponse, HoldResponse
from app.services.bookings import (
    confirm_booking_service,
    generate_price_lock_token,
    get_booking_by_pnr,
    hold_seat_service,
    validate_price_lock_token,
)
from app.utils.idempotency import get_idempotency_manager


def test_price_lock_token_valid() -> None:
    flight_id = str(uuid4())
    token = generate_price_lock_token(flight_id, FareClassEnum.FLEXIBLE, 25000)
    validate_price_lock_token(token, flight_id, FareClassEnum.FLEXIBLE, 25000)


def test_price_lock_token_expired() -> None:
    flight_id = str(uuid4())
    past_ts = datetime.now(timezone.utc).timestamp() - (16 * 60)
    token = generate_price_lock_token(flight_id, FareClassEnum.BASIC_ECONOMY, 15000, timestamp=past_ts)
    with pytest.raises(HTTPException) as exc_info:
        validate_price_lock_token(token, flight_id, FareClassEnum.BASIC_ECONOMY, 15000)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Price lock expired, please re-search"


def test_price_lock_token_tampered_signature() -> None:
    flight_id = str(uuid4())
    token = generate_price_lock_token(flight_id, FareClassEnum.FLEXIBLE, 25000)
    tampered_token = token[:-4] + "ffff"
    with pytest.raises(HTTPException) as exc_info:
        validate_price_lock_token(tampered_token, flight_id, FareClassEnum.FLEXIBLE, 25000)
    assert exc_info.value.status_code == 400
    assert "signature" in exc_info.value.detail.lower()


def test_hold_seat_service_success() -> None:
    mock_supabase = MagicMock()
    hold_id = str(uuid4())
    rpc_call = MagicMock()
    rpc_call.execute = AsyncMock(return_value=MagicMock(data=hold_id))
    mock_supabase.rpc.return_value = rpc_call

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    now_iso = datetime.now(timezone.utc).isoformat()
    table_chain.execute = AsyncMock(return_value=MagicMock(data={
        "id": hold_id,
        "expires_at": now_iso,
        "flight_seats": {"seat_number": "3C"},
    }))
    mock_supabase.table.return_value = table_chain

    res = asyncio.run(hold_seat_service(
        supabase=mock_supabase,
        flight_id=str(uuid4()),
        seat_id=str(uuid4()),
        session_id="sess-alpha",
        hold_minutes=15,
    ))
    assert res["hold_id"] == hold_id
    assert res["seat_number"] == "3C"
    assert res["status"] == "HELD"
    mock_supabase.rpc.assert_called_once()


def test_hold_seat_service_conflict() -> None:
    mock_supabase = MagicMock()
    rpc_call = MagicMock()
    rpc_call.execute = AsyncMock(side_effect=Exception("Seat 1A is not available - 55P03"))
    mock_supabase.rpc.return_value = rpc_call

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(hold_seat_service(
            supabase=mock_supabase,
            flight_id=str(uuid4()),
            seat_id=str(uuid4()),
            session_id="sess-beta",
        ))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Seat is no longer available"


def test_confirm_booking_service_success() -> None:
    mock_supabase = MagicMock()
    flight_id = str(uuid4())
    token = generate_price_lock_token(flight_id, FareClassEnum.FLEXIBLE, 25000)

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.upsert.return_value = table_chain
    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data=None),
        MagicMock(data={"flight_number": "PK-301"}),
        MagicMock(data=None),
    ])
    mock_supabase.table.return_value = table_chain

    booking_id = str(uuid4())
    rpc_call = MagicMock()
    rpc_call.execute = AsyncMock(return_value=MagicMock(data={
        "booking_id": booking_id,
        "pnr": "XYZ123",
        "flight_id": flight_id,
        "seat_id": str(uuid4()),
        "seat_number": "5A",
        "passenger_name": "Jane Doe",
        "passenger_email": "jane@example.com",
        "fare_class": "LEXIBLE",
        "fare_paid_cents":25000,
        "status": "CONFIRMED",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }))
    mock_supabase.rpc.return_value = rpc_call

    payload = BookingConfirmRequest(
        hold_id=uuid4(),
        passenger_name="Jane Doe",
        passenger_email="jane@example.com",
        fare_class=FareClassEnum.FLEXIBLE,
        fare_cents=25000,
        price_lock_token=token,
    )

    res = asyncio.run(confirm_booking_service(
        supabase=mock_supabase,
        payload=payload,
        idempotency_key="idem-confirm-001",
        client_ip="192.168.1.1",
    ))
    assert res["pnr"] == "XYZ123"
    assert res["flight_number"] == "PK-301"
    assert res["status"] == "CONFIRMED"


def test_confirm_booking_idempotent_replay() -> None:
    mock_supabase = MagicMock()
    cached_payload = {
        "booking_id": str(uuid4()),
        "pnr": "CACHED",
        "flight_number": "PK-100",
        "passenger_name": "John Doe",
        "passenger_email": "john@example.com",
        "fare_class": "FLEXIBLE",
        "fare_paid_cents":20000,
        "seat_number": "1B",
        "status": "CONFIRMED",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(return_value=MagicMock(data={"response_body": cached_payload}))
    mock_supabase.table.return_value = table_chain

    payload = BookingConfirmRequest(
        hold_id=uuid4(),
        passenger_name="John Doe",
        passenger_email="john@example.com",
        fare_class=FareClassEnum.FLEXIBLE,
        fare_cents=20000,
        price_lock_token="token-xyz-987",
    )

    res = asyncio.run(confirm_booking_service(
        supabase=mock_supabase,
        payload=payload,
        idempotency_key="cached-key-001",
        client_ip="127.0.0.1",
    ))
    assert res["pnr"] == "CACHED"
    mock_supabase.rpc.assert_not_called()


def test_get_booking_by_pnr_not_found() -> None:
    mock_supabase = MagicMock()
    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(return_value=MagicMock(data=None))
    mock_supabase.table.return_value = table_chain

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_booking_by_pnr(mock_supabase, "NOTFND"))
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


def test_post_hold_endpoint_201() -> None:
    mock_supabase = MagicMock()
    hold_id = str(uuid4())
    rpc_call = MagicMock()
    rpc_call.execute = AsyncMock(return_value=MagicMock(data=hold_id))
    mock_supabase.rpc.return_value = rpc_call

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.upsert.return_value = table_chain
    table_chain.execute = AsyncMock(return_value=MagicMock(data={
        "id": hold_id,
        "expires_at": datetime.now(timezone.utc).isoformat(),
        "flight_seats": {"seat_number": "7A"},
    }))
    mock_supabase.table.return_value = table_chain

    app.dependency_overrides[get_supabase_client] = lambda: mock_supabase
    client = TestClient(app)

    res = client.post(
        "/api/v1/bookings/hold",
        headers={"Idempotency-Key": "test-route-hold-001"},
        json={
            "flight_id": str(uuid4()),
            "seat_id": str(uuid4()),
            "session_id": "sess-integration",
            "hold_minutes": 10,
        },
    )
    app.dependency_overrides.clear()

    assert res.status_code == 201
    body = res.json()
    assert body["hold_id"] == hold_id
    assert body["seat_number"] == "7A"
    assert body["status"] == "HELD"

def test_post_confirm_endpoint_201() -> None:
    mock_supabase = MagicMock()
    flight_id = str(uuid4())
    booking_id = str(uuid4())
    token = generate_price_lock_token(flight_id, FareClassEnum.PREMIUM_FIRST, 50000)

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.upsert.return_value = table_chain
    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data=None),
        MagicMock(data={"flight_number": "PK-701"}),
        MagicMock(data=None),
    ])
    mock_supabase.table.return_value = table_chain

    rpc_call = MagicMock()
    rpc_call.execute = AsyncMock(return_value=MagicMock(data={
        "booking_id": booking_id,
        "pnr": "CONFIR",
        "flight_id": flight_id,
        "seat_id": str(uuid4()),
        "seat_number": "1A",
        "passenger_name": "VIP Traveler",
        "passenger_email": "vip@example.com",
        "fare_class": "PREMIUM_FIRST",
        "fare_paid_cents": 50000,
        "status": "CONFIRMED",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }))
    mock_supabase.rpc.return_value = rpc_call

    app.dependency_overrides[get_supabase_client] = lambda: mock_supabase
    client = TestClient(app)

    res = client.post(
        "/api/v1/bookings/confirm",
        headers={"Idempotency-Key": "test-route-confirm-001"},
        json={
            "hold_id": str(uuid4()),
            "passenger_name": "VIP Traveler",
            "passenger_email": "vip@example.com",
            "fare_class": FareClassEnum.PREMIUM_FIRST,
            "fare_cents": 50000,
            "price_lock_token": token,
        },
    )
    app.dependency_overrides.clear()

    assert res.status_code == 201
    body = res.json()
    assert body["idempotent"] if "idempotent" in body else True
    assert body["pnr"] == "CONFIR"
    assert body["flight_number"] == "PK-701"
    assert body["seat_number"] == "1A"
    assert body["status"] == "CONFIRMED"