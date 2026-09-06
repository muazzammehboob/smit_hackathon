"""Unit and integration tests for Stage 2C Admin Routes and Flight Inventory CRUD."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.models.requests import FlightCreateRequest
from app.routes.admin import CapacityAdjustPayload
from app.services.flights import (
    adjust_capacity_service,
    create_flight_service,
    generate_class_seats,
    get_flight_admin_service,
    list_flights_admin_service,
)


# ==============================================================================
# 1. Physical Seat Generator Tests
# ==============================================================================
def test_generate_class_seats_ranges() -> None:
    """Validate seat generation generates sequential rows within designated bounds."""
    flight_id = str(uuid4())

    # First Class: 1A..4F (20 seats requested)
    first_seats = generate_class_seats(
        start_row=1, count=20, seat_class="FIRST", flight_id=flight_id
    )
    assert len(first_seats) == 20
    assert first_seats[0]["seat_number"] == "1A"
    assert first_seats[5]["seat_number"] == "1F"
    assert first_seats[6]["seat_number"] == "2A"
    assert first_seats[-1]["seat_number"] == "4B"
    assert all(s["seat_class"] == "FIRST" for s in first_seats)
    assert all(s["status"] == "AVAILABLE" for s in first_seats)

    # Business Class: 10A..15F (30 seats requested)
    biz_seats = generate_class_seats(
        start_row=10, count=30, seat_class="BUSINESS", flight_id=flight_id
    )
    assert len(biz_seats) == 30
    assert biz_seats[0]["seat_number"] == "10A"
    assert biz_seats[5]["seat_number"] == "10F"
    assert biz_seats[-1]["seat_number"] == "14F"
    assert all(s["seat_class"] == "BUSINESS" for s in biz_seats)

    # Economy Class: 20A..35F (50 seats requested)
    econ_seats = generate_class_seats(
        start_row=20, count=50, seat_class="ECONOMY", flight_id=flight_id
    )
    assert len(econ_seats) == 50
    assert econ_seats[0]["seat_number"] == "20A"
    assert econ_seats[-1]["seat_number"] == "28B"
    assert all(s["seat_class"] == "ECONOMY" for s in econ_seats)

    # Zero seats requested returns empty list
    assert generate_class_seats(start_row=1, count=0, seat_class="FIRST", flight_id=flight_id) == []


# ==============================================================================
# 2. create_flight_service Tests
# ==============================================================================
def test_create_flight_service_duplicate_rejection() -> None:
    """Verify duplicate flight detection raises HTTP 409 Conflict."""
    mock_supabase = MagicMock()
    # Mock table("flights").select("id").eq(...).gte(...).lte(...).execute()
    query_chain = MagicMock()
    query_chain.select.return_value = query_chain
    query_chain.eq.return_value = query_chain
    query_chain.gte.return_value = query_chain
    query_chain.lte.return_value = query_chain

    # Simulate existing flight on that date
    query_chain.execute = AsyncMock(return_value=MagicMock(data=[{"id": str(uuid4())}]))
    mock_supabase.table.return_value = query_chain

    payload = FlightCreateRequest(
        flight_number="BA101",
        origin_airport="LHR",
        destination_airport="DXB",
        departure_time=datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc),
        arrival_time=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        total_capacity=100,
        first_class_seats=20,
        business_class_seats=30,
        economy_class_seats=50,
    )

    async def run() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await create_flight_service(mock_supabase, payload, admin_id="admin-001")
        assert exc_info.value.status_code == 409
        assert "already scheduled" in exc_info.value.detail

    asyncio.run(run())


def test_create_flight_service_success() -> None:
    """Verify flight creation inserts flight, classes, seats, and audit log."""
    mock_supabase = MagicMock()
    flight_id = str(uuid4())

    def mock_table(table_name: str):
        table_mock = MagicMock()
        if table_name == "flights":
            select_mock = MagicMock()
            select_mock.eq.return_value = select_mock
            select_mock.gte.return_value = select_mock
            select_mock.lte.return_value = select_mock
            select_mock.execute = AsyncMock(return_value=MagicMock(data=[]))
            table_mock.select.return_value = select_mock

            insert_mock = MagicMock()
            insert_mock.execute = AsyncMock(
                return_value=MagicMock(
                    data=[
                        {
                            "id": flight_id,
                            "flight_number": "BA101",
                            "origin_airport": "LHR",
                            "destination_airport": "DXB",
                            "departure_time": "2026-10-01T05:00:00Z",
                            "arrival_time": "2026-10-01T12:00:00Z",
                            "total_capacity": 100,
                            "status": "SCHEDULED",
                            "schedule_version": 1,
                        }
                    ]
                )
            )
            table_mock.insert.return_value = insert_mock
        else:
            insert_mock = MagicMock()
            insert_mock.execute = AsyncMock(
                return_value=MagicMock(data=[{"id": str(uuid4())}])
            )
            table_mock.insert.return_value = insert_mock
        return table_mock

    mock_supabase.table.side_effect = mock_table

    payload = FlightCreateRequest(
        flight_number="BA101",
        origin_airport="LHR",
        destination_airport="DXB",
        departure_time=datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc),
        arrival_time=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        total_capacity=100,
        first_class_seats=20,
        business_class_seats=30,
        economy_class_seats=50,
    )

    async def run() -> None:
        result = await create_flight_service(mock_supabase, payload, admin_id="admin-001")
        assert result["flight_id"] == flight_id
        assert result["total_capacity"] == 100
        assert result["seats_generated"] == 100

    asyncio.run(run())


# ==============================================================================
# 3. adjust_capacity_service Tests
# ==============================================================================
def test_adjust_capacity_service_guard_violation() -> None:
    """Verify that shrinking capacity below booked seats raises HTTP 400 Bad Request."""
    mock_supabase = MagicMock()
    flight_id = uuid4()

    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    chain.execute = AsyncMock(
        return_value=MagicMock(
            data={
                "id": str(uuid4()),
                "flight_id": str(flight_id),
                "seat_class": "ECONOMY",
                "capacity": 70,
                "booked_seats": 50,
            }
        )
    )
    mock_supabase.table.return_value = chain

    async def run() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await adjust_capacity_service(
                supabase=mock_supabase,
                flight_id=flight_id,
                seat_class="ECONOMY",
                new_capacity=40,
                admin_id="admin-001",
            )
        assert exc_info.value.status_code == 400
        assert "Cannot reduce" in exc_info.value.detail
        assert "50" in exc_info.value.detail

    asyncio.run(run())


def test_adjust_capacity_service_success() -> None:
    """Verify capacity update succeeds and recalculates flights total_capacity."""
    mock_supabase = MagicMock()
    flight_id = uuid4()
    class_id = str(uuid4())

    select_count = 0

    def mock_table(table_name: str):
        nonlocal select_count
        table_mock = MagicMock()
        if table_name == "flight_classes":
            def on_select(*args, **kwargs):
                nonlocal select_count
                select_count += 1
                s = MagicMock()
                s.eq.return_value = s
                if select_count == 1:
                    s.maybe_single.return_value.execute = AsyncMock(
                        return_value=MagicMock(
                            data={
                                "id": class_id,
                                "flight_id": str(flight_id),
                                "seat_class": "ECONOMY",
                                "capacity": 50,
                                "booked_seats": 30,
                            }
                        )
                    )
                else:
                    s.execute = AsyncMock(
                        return_value=MagicMock(
                            data=[
                                {"capacity": 20},
                                {"capacity": 30},
                                {"capacity": 60},
                            ]
                        )
                    )
                return s

            table_mock.select.side_effect = on_select
            update_mock = MagicMock()
            update_mock.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"id": class_id}])
            )
            table_mock.update.return_value = update_mock
        elif table_name == "flights":
            update_mock = MagicMock()
            update_mock.eq.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{"id": str(flight_id)}])
            )
            table_mock.update.return_value = update_mock
        elif table_name == "admin_audit_logs":
            insert_mock = MagicMock()
            insert_mock.execute = AsyncMock(
                return_value=MagicMock(data=[{"id": str(uuid4())}])
            )
            table_mock.insert.return_value = insert_mock
        return table_mock

    mock_supabase.table.side_effect = mock_table

    async def run() -> None:
        result = await adjust_capacity_service(
            supabase=mock_supabase,
            flight_id=flight_id,
            seat_class="ECONOMY",
            new_capacity=60,
            admin_id="admin-001",
        )
        assert result["capacity"] == 60
        assert result["total_capacity"] == 110
        assert result["seat_class"] == "ECONOMY"

    asyncio.run(run())


# ==============================================================================
# 4. Route Integration Tests via TestClient
# ==============================================================================
client = TestClient(app)


def test_admin_routes_rbac_unauthenticated() -> None:
    """Verify accessing admin routes without auth headers yields HTTP 401."""
    res_post = client.post("/api/v1/admin/flights", json={})
    assert res_post.status_code == 401

    res_get = client.get("/api/v1/admin/flights")
    assert res_get.status_code == 401


def test_admin_routes_rbac_forbidden_for_regular_user() -> None:
    """Verify regular user token receives HTTP 403 Forbidden."""
    headers = {"Authorization": "Bearer test-token"}  # regular user token
    res = client.get("/api/v1/admin/flights", headers=headers)
    assert res.status_code == 403


def test_admin_capacity_payload_validation() -> None:
    """Verify CapacityAdjustPayload normalizes case and rejects invalid seat classes."""
    valid = CapacityAdjustPayload(seat_class="economy", new_capacity=60)
    assert valid.seat_class == "ECONOMY"
    assert valid.new_capacity == 60

    with pytest.raises(ValueError):
        CapacityAdjustPayload(seat_class="INVALID_CLASS", new_capacity=50)

    with pytest.raises(ValueError):
        CapacityAdjustPayload(seat_class="FIRST", new_capacity=-1)

    with pytest.raises(ValueError):
        CapacityAdjustPayload(seat_class="FIRST", new_capacity=0)


def test_require_role_jwt_and_header_behavior() -> None:
    """Validate require_role JWT validation, header fallback in dev, and forgery protection."""
    from starlette.requests import Request
    from app.config import settings
    from app.middleware.rbac import require_role
    from app.routes.auth import create_jwt_token

    admin_token = create_jwt_token({"sub": "admin-123", "email": "adm@aloft.com", "role": "SUPER_ADMIN"})
    passenger_token = create_jwt_token({"sub": "user-456", "email": "user@aloft.com", "role": "PASSENGER"})

    async def run() -> None:
        checker = require_role(["SUPER_ADMIN"])

        # 1. Valid JWT with matching role succeeds
        req_admin = Request({
            "type": "http",
            "headers": [(b"authorization", f"Bearer {admin_token}".encode("utf-8"))],
        })
        user = await checker(req_admin)
        assert user["id"] == "admin-123"
        assert user["role"] == "SUPER_ADMIN"
        assert user["email"] == "adm@aloft.com"

        # 2. Valid JWT with non-matching role raises 403
        req_passenger = Request({
            "type": "http",
            "headers": [(b"authorization", f"Bearer {passenger_token}".encode("utf-8"))],
        })
        with pytest.raises(HTTPException) as exc_p:
            await checker(req_passenger)
        assert exc_p.value.status_code == 403

        # 3. Forgery attempt: Passenger JWT + X-Admin-Role header must still raise 403
        req_forged = Request({
            "type": "http",
            "headers": [
                (b"authorization", f"Bearer {passenger_token}".encode("utf-8")),
                (b"x-admin-role", b"SUPER_ADMIN"),
            ],
        })
        with pytest.raises(HTTPException) as exc_f:
            await checker(req_forged)
        assert exc_f.value.status_code == 403

        # 4. Dev/test mode fallback to X-Admin-Role when no token provided
        req_dev_header = Request({
            "type": "http",
            "headers": [(b"x-admin-role", b"SUPER_ADMIN")],
        })
        dev_user = await checker(req_dev_header)
        assert dev_user["role"] == "SUPER_ADMIN"

        # 5. Production mode rejects X-Admin-Role header fallback
        orig_env = settings.ENVIRONMENT
        try:
            settings.ENVIRONMENT = "production"
            with pytest.raises(HTTPException) as exc_prod:
                await checker(req_dev_header)
            assert exc_prod.value.status_code == 403
        finally:
            settings.ENVIRONMENT = orig_env

    asyncio.run(run())
