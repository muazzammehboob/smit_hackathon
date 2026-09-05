"""Unit tests for the health check endpoint and core scaffold utilities."""

import string
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.utils.pnr import generate_pnr, is_valid_pnr
from app.utils.idempotency import IdempotencyManager, IdempotencyRecord


client = TestClient(app)


def test_health_check_returns_200() -> None:
    """Verify that GET /health returns HTTP 200 with the correct status payload."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "flight-management-api",
    }


def test_api_v1_health_returns_200() -> None:
    """Verify that GET /api/v1/health returns HTTP 200 with the correct status payload."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "flight-management-api",
    }


def test_pnr_generator_length_and_charset() -> None:
    """Ensure generated PNRs are 6 chars, uppercase, and omit 0, O, 1, I."""
    forbidden_chars = {"0", "O", "1", "I"}
    for _ in range(50):
        pnr = generate_pnr()
        assert len(pnr) == 6
        assert pnr.isupper()
        assert all(char not in forbidden_chars for char in pnr)
        assert is_valid_pnr(pnr)


def test_is_valid_pnr_validation() -> None:
    """Test the PNR format validation helper."""
    assert is_valid_pnr("ABC234") is True
    assert is_valid_pnr("ABC012") is False  # contains 0 and 1
    assert is_valid_pnr("ABCD") is False  # too short
    assert is_valid_pnr("ABCDEFGH") is False  # too long


def test_idempotency_manager_in_memory() -> None:
    """Test in-memory idempotency record storage and retrieval."""
    import asyncio

    async def run_test() -> None:
        manager = IdempotencyManager()
        await manager.clear_memory_cache()

        key = "test-idem-key-12345"
        payload = {"booking_id": "bk-999", "status": "confirmed"}

        saved = await manager.save_record(key=key, status_code=201, response_data=payload)
        assert saved.key == key
        assert saved.status_code == 201
        assert saved.response_data == payload

        retrieved = await manager.get_record(key=key)
        assert retrieved is not None
        assert retrieved.key == key
        assert retrieved.status_code == 201
        assert retrieved.response_data == payload

        missing = await manager.get_record(key="non-existent-key")
        assert missing is None

    asyncio.run(run_test())


def test_standardized_not_found_exception() -> None:
    """Verify that 404 errors return standardized error structure."""
    response = client.get("/non-existent-route")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert "error_code" in data
    assert data["error_code"] == "HTTP_404"
