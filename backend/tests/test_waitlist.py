"""Comprehensive unit and integration tests for Waitlist Subsystem (Domain 5)."""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_supabase_client
from app.models.requests import LoyaltyTierEnum, SeatClassEnum, WaitlistJoinRequest
from app.models.responses import WaitlistResponse
from app.services.waitlist import (
    claim_waitlist_seat_service,
    compute_priority_score,
    get_waitlist_position_service,
    join_waitlist_service,
    leave_waitlist_service,
    WaitlistService,
)


def test_compute_priority_score_ordering() -> None:
    now = datetime.now(timezone.utc)
    # Higher tier beats lower tier
    plat_score = compute_priority_score(LoyaltyTierEnum.PLATINUM, SeatClassEnum.ECONOMY, now)
    gold_score = compute_priority_score(LoyaltyTierEnum.GOLD, SeatClassEnum.FIRST, now)
    silv_score = compute_priority_score(LoyaltyTierEnum.SILVER, SeatClassEnum.FIRST, now)
    gen_score = compute_priority_score(LoyaltyTierEnum.GENERAL, SeatClassEnum.FIRST, now)
    assert plat_score > gold_score > silv_score > gen_score

    # Same tier: higher class beats lower class
    gold_first = compute_priority_score(LoyaltyTierEnum.GOLD, SeatClassEnum.FIRST, now)
    gold_biz = compute_priority_score(LoyaltyTierEnum.GOLD, SeatClassEnum.BUSINESS, now)
    gold_econ = compute_priority_score(LoyaltyTierEnum.GOLD, SeatClassEnum.ECONOMY, now)
    assert gold_first > gold_biz > gold_econ

    # Same tier and class: earlier request (past timestamp) beats later request
    earlier = now - timedelta(hours=5)
    score_earlier = compute_priority_score(LoyaltyTierEnum.GOLD, SeatClassEnum.ECONOMY, earlier)
    score_later = compute_priority_score(LoyaltyTierEnum.GOLD, SeatClassEnum.ECONOMY, now)
    assert score_earlier > score_later

    # Positive scale
    assert gen_score > 0


def test_join_waitlist_flight_not_found() -> None:
    mock_supabase = MagicMock()
    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(return_value=MagicMock(data=None))
    mock_supabase.table.return_value = table_chain

    payload = WaitlistJoinRequest(
        flight_id=uuid4(),
        requested_class=SeatClassEnum.ECONOMY,
        passenger_name="Alice Brown",
        passenger_email="alice@example.com",
        loyalty_tier=LoyaltyTierEnum.GOLD,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(join_waitlist_service(supabase=mock_supabase, payload=payload))
    assert exc_info.value.status_code == 404
    assert "Flight not found" in exc_info.value.detail


def test_join_waitlist_class_not_found() -> None:
    mock_supabase = MagicMock()
    flight_id = uuid4()

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    # Flight exists, but flight_classes returns None
    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={"id": str(flight_id)}),
        MagicMock(data=None),
    ])
    mock_supabase.table.return_value = table_chain

    payload = WaitlistJoinRequest(
        flight_id=flight_id,
        requested_class=SeatClassEnum.FIRST,
        passenger_name="Alice Brown",
        passenger_email="alice@example.com",
        loyalty_tier=LoyaltyTierEnum.GOLD,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(join_waitlist_service(supabase=mock_supabase, payload=payload))
    assert exc_info.value.status_code == 404
    assert "Flight class" in exc_info.value.detail


def test_join_waitlist_seats_still_available() -> None:
    mock_supabase = MagicMock()
    flight_id = uuid4()

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.gt.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    # Flight exists, capacity=100, booked=50, holds=0 -> available=50 > 0
    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={"id": str(flight_id)}),
        MagicMock(data={"capacity": 100, "booked_seats": 50}),
        MagicMock(data=[]),
    ])
    mock_supabase.table.return_value = table_chain

    payload = WaitlistJoinRequest(
        flight_id=flight_id,
        requested_class=SeatClassEnum.ECONOMY,
        passenger_name="Alice Brown",
        passenger_email="alice@example.com",
        loyalty_tier=LoyaltyTierEnum.GENERAL,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(join_waitlist_service(supabase=mock_supabase, payload=payload))
    assert exc_info.value.status_code == 400
    assert "Seats are still available for booking, cannot join waitlist" in exc_info.value.detail


def test_join_waitlist_success() -> None:
    mock_supabase = MagicMock()
    flight_id = uuid4()
    waitlist_id = str(uuid4())

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.gt.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.insert.return_value = table_chain

    # 1. Flight lookup: exists
    # 2. Flight class lookup: capacity=50, booked=48
    # 3. Active holds: 2 holds in ECONOMY -> available = 50 - (48 + 2) = 0 (Full!)
    # 4. Insert waitlist record
    # 5. Query position: count of higher scores = 2 -> position = 3
    pos_mock = MagicMock()
    pos_mock.count = 2
    pos_mock.data = [{"id": str(uuid4())}, {"id": str(uuid4())}]

    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={"id": str(flight_id)}),
        MagicMock(data={"capacity": 50, "booked_seats": 48}),
        MagicMock(data=[
            {"flight_seats": {"seat_class": "ECONOMY"}},
            {"flight_seats": {"seat_class": "ECONOMY"}},
        ]),
        MagicMock(data=[{"id": waitlist_id}]),
        pos_mock,
    ])
    mock_supabase.table.return_value = table_chain

    payload = WaitlistJoinRequest(
        flight_id=flight_id,
        requested_class=SeatClassEnum.ECONOMY,
        passenger_name="John Platinum",
        passenger_email="platinum@example.com",
        loyalty_tier=LoyaltyTierEnum.PLATINUM,
    )

    res = asyncio.run(join_waitlist_service(supabase=mock_supabase, payload=payload))
    assert res["waitlist_id"] == waitlist_id
    assert res["flight_id"] == str(flight_id)
    assert res["passenger_name"] == "John Platinum"
    assert res["loyalty_tier"] == "PLATINUM"
    assert res["status"] == "WAITING"
    assert res["position"] == 3
    assert res["priority_score"] > 0


def test_get_waitlist_position_not_found() -> None:
    mock_supabase = MagicMock()
    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(return_value=MagicMock(data=None))
    mock_supabase.table.return_value = table_chain

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_waitlist_position_service(mock_supabase, str(uuid4()), "missing@example.com"))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Active waitlist entry not found"


def test_get_waitlist_position_success() -> None:
    mock_supabase = MagicMock()
    flight_id = str(uuid4())
    waitlist_id = str(uuid4())

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.gt.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain

    pos_mock = MagicMock()
    pos_mock.count = 0
    pos_mock.data = []

    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={
            "id": waitlist_id,
            "flight_id": flight_id,
            "passenger_email": "first@example.com",
            "priority_score": 3000000,
            "status": "WAITING",
        }),
        pos_mock,
    ])
    mock_supabase.table.return_value = table_chain

    res = asyncio.run(get_waitlist_position_service(mock_supabase, flight_id, "first@example.com"))
    assert res["position"] == 1
    assert res["waitlist_id"] == waitlist_id


def test_leave_waitlist_not_found() -> None:
    mock_supabase = MagicMock()
    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(return_value=MagicMock(data=None))
    mock_supabase.table.return_value = table_chain

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(leave_waitlist_service(mock_supabase, str(uuid4())))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Waitlist entry not found"


def test_leave_waitlist_success() -> None:
    mock_supabase = MagicMock()
    waitlist_id = str(uuid4())

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.update.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={"id": waitlist_id, "status": "WAITING"}),
        MagicMock(data=[{"id": waitlist_id, "status": "CANCELLED"}]),
    ])
    mock_supabase.table.return_value = table_chain

    res = asyncio.run(leave_waitlist_service(mock_supabase, waitlist_id))
    assert res["waitlist_id"] == waitlist_id
    assert res["status"] == "CANCELLED"
    assert res["message"] == "Successfully left the waitlist"


def test_claim_waitlist_seat_not_found() -> None:
    mock_supabase = MagicMock()
    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(return_value=MagicMock(data=None))
    mock_supabase.table.return_value = table_chain

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(claim_waitlist_seat_service(mock_supabase, str(uuid4())))
    assert exc_info.value.status_code == 404


def test_claim_waitlist_seat_not_promoted() -> None:
    mock_supabase = MagicMock()
    waitlist_id = str(uuid4())

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(return_value=MagicMock(data={
        "id": waitlist_id,
        "status": "WAITING",
    }))
    mock_supabase.table.return_value = table_chain

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(claim_waitlist_seat_service(mock_supabase, waitlist_id))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Waitlist entry is not promoted for claiming"


def test_claim_waitlist_seat_expired() -> None:
    mock_supabase = MagicMock()
    waitlist_id = str(uuid4())
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.update.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={
            "id": waitlist_id,
            "status": "PROMOTED",
            "claim_deadline": past_iso,
            "hold_id": str(uuid4()),
        }),
        MagicMock(data=None),
    ])
    mock_supabase.table.return_value = table_chain

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(claim_waitlist_seat_service(mock_supabase, waitlist_id))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Waitlist claim window has expired"


def test_claim_waitlist_seat_success() -> None:
    mock_supabase = MagicMock()
    waitlist_id = str(uuid4())
    hold_id = str(uuid4())
    flight_id = str(uuid4())
    future_iso = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.update.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={
            "id": waitlist_id,
            "flight_id": flight_id,
            "passenger_name": "Promoted Passenger",
            "passenger_email": "promoted@example.com",
            "status": "PROMOTED",
            "requested_class": "BUSINESS",
            "hold_id": hold_id,
            "claim_deadline": future_iso,
        }),
        MagicMock(data={"base_price_cents": 35000}),
        MagicMock(data=[{"id": waitlist_id, "status": "CONVERTED"}]),
    ])
    mock_supabase.table.return_value = table_chain

    rpc_call = MagicMock()
    rpc_call.execute = AsyncMock(return_value=MagicMock(data={
        "booking_id": str(uuid4()),
        "pnr": "PROMO1",
        "seat_number": "2A",
        "fare_class": "FLEXIBLE",
        "fare_paid_cents": 35000,
        "status": "CONFIRMED",
    }))
    mock_supabase.rpc.return_value = rpc_call

    res = asyncio.run(claim_waitlist_seat_service(mock_supabase, waitlist_id, client_ip="10.0.0.1"))
    assert res["pnr"] == "PROMO1"
    assert res["status"] == "CONFIRMED"
    assert res["waitlist_id"] == waitlist_id


def test_waitlist_service_class_wrapper() -> None:
    mock_supabase = MagicMock()
    service = WaitlistService(supabase_client=mock_supabase)
    assert service.supabase == mock_supabase


def test_api_readiness_probe() -> None:
    client = TestClient(app)
    res = client.get("/api/v1/waitlist/")
    assert res.status_code == 200
    assert res.json() == {"status": "operational", "module": "waitlist"}


def test_api_route_join_waitlist_201() -> None:
    mock_supabase = MagicMock()
    flight_id = str(uuid4())
    waitlist_id = str(uuid4())

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.gt.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.insert.return_value = table_chain

    pos_mock = MagicMock()
    pos_mock.count = 0
    pos_mock.data = []

    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={"id": flight_id}),
        MagicMock(data={"capacity": 20, "booked_seats": 20}),
        MagicMock(data=[]),
        MagicMock(data=[{"id": waitlist_id}]),
        pos_mock,
    ])
    mock_supabase.table.return_value = table_chain

    app.dependency_overrides[get_supabase_client] = lambda: mock_supabase
    client = TestClient(app)

    res = client.post(
        "/api/v1/waitlist/join",
        json={
            "flight_id": flight_id,
            "requested_class": "BUSINESS",
            "passenger_name": "Integration User",
            "passenger_email": "user@example.com",
            "loyalty_tier": "GOLD",
        },
    )
    app.dependency_overrides.clear()

    assert res.status_code == 201
    body = res.json()
    assert body["waitlist_id"] == waitlist_id
    assert body["flight_id"] == flight_id
    assert body["passenger_name"] == "Integration User"
    assert body["loyalty_tier"] == "GOLD"
    assert body["status"] == "WAITING"
    assert body["position"] == 1


def test_api_route_position_200() -> None:
    mock_supabase = MagicMock()
    flight_id = str(uuid4())
    waitlist_id = str(uuid4())

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.gt.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain

    pos_mock = MagicMock()
    pos_mock.count = 1
    pos_mock.data = [{"id": str(uuid4())}]

    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={
            "id": waitlist_id,
            "flight_id": flight_id,
            "passenger_name": "Queued Passenger",
            "passenger_email": "queued@example.com",
            "loyalty_tier": "SILVER",
            "requested_class": "ECONOMY",
            "priority_score": 100000,
            "status": "WAITING",
        }),
        pos_mock,
    ])
    mock_supabase.table.return_value = table_chain

    app.dependency_overrides[get_supabase_client] = lambda: mock_supabase
    client = TestClient(app)

    res = client.get(f"/api/v1/waitlist/position?flight_id={flight_id}&email=queued@example.com")
    app.dependency_overrides.clear()

    assert res.status_code == 200
    body = res.json()
    assert body["waitlist_id"] == waitlist_id
    assert body["position"] == 2
    assert body["status"] == "WAITING"


def test_api_route_leave_200() -> None:
    mock_supabase = MagicMock()
    waitlist_id = str(uuid4())

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.update.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={"id": waitlist_id, "status": "WAITING"}),
        MagicMock(data=[{"id": waitlist_id, "status": "CANCELLED"}]),
    ])
    mock_supabase.table.return_value = table_chain

    app.dependency_overrides[get_supabase_client] = lambda: mock_supabase
    client = TestClient(app)

    res = client.delete(f"/api/v1/waitlist/{waitlist_id}")
    app.dependency_overrides.clear()

    assert res.status_code == 200
    body = res.json()
    assert body["waitlist_id"] == waitlist_id
    assert body["status"] == "CANCELLED"
    assert "Successfully left" in body["message"]


def test_api_route_claim_200() -> None:
    mock_supabase = MagicMock()
    waitlist_id = str(uuid4())
    hold_id = str(uuid4())
    flight_id = str(uuid4())
    future_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.eq.return_value = table_chain
    table_chain.update.return_value = table_chain
    table_chain.maybe_single.return_value = table_chain
    table_chain.execute = AsyncMock(side_effect=[
        MagicMock(data={
            "id": waitlist_id,
            "flight_id": flight_id,
            "passenger_name": "Promoted Passenger",
            "passenger_email": "promoted@example.com",
            "status": "PROMOTED",
            "requested_class": "FIRST",
            "hold_id": hold_id,
            "claim_deadline": future_iso,
        }),
        MagicMock(data={"base_price_cents": 50000}),
        MagicMock(data=[{"id": waitlist_id, "status": "CONVERTED"}]),
    ])
    mock_supabase.table.return_value = table_chain

    rpc_call = MagicMock()
    rpc_call.execute = AsyncMock(return_value=MagicMock(data={
        "booking_id": str(uuid4()),
        "pnr": "CLAIM1",
        "seat_number": "1A",
        "fare_class": "PREMIUM_FIRST",
        "fare_paid_cents": 50000,
        "status": "CONFIRMED",
    }))
    mock_supabase.rpc.return_value = rpc_call

    app.dependency_overrides[get_supabase_client] = lambda: mock_supabase
    client = TestClient(app)

    res = client.post(
        f"/api/v1/waitlist/{waitlist_id}/claim",
        headers={"X-Forwarded-For": "203.0.113.195"},
    )
    app.dependency_overrides.clear()

    assert res.status_code == 200
    body = res.json()
    assert body["pnr"] == "CLAIM1"
    assert body["status"] == "CONFIRMED"
    assert body["waitlist_id"] == waitlist_id
