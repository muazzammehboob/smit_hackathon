"""Unit tests for email dispatch utility and webhook approval integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.utils.email import (
    send_transactional_email,
    send_booking_confirmation,
    send_cancellation_receipt,
    send_approved_rag_response,
)
from app.routes.webhooks import ApprovalRequest, support_approve


def test_send_transactional_email_without_supabase():
    result = asyncio.run(send_transactional_email(
        recipient="test@example.com",
        subject="Test Subject",
        body="Hello World",
    ))
    assert result == {
        "status": "sent",
        "recipient": "test@example.com",
        "subject": "Test Subject",
    }


def test_send_transactional_email_with_supabase():
    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_insert = MagicMock()
    mock_insert.execute = AsyncMock(return_value=MagicMock(data=[{"id": "1"}]))
    mock_table.insert.return_value = mock_insert
    mock_supabase.table.return_value = mock_table

    result = asyncio.run(send_transactional_email(
        recipient="user@example.com",
        subject="Booking Receipt",
        body="Booking details here...",
        email_type="BOOKING_CONFIRMATION",
        booking_id="b-123",
        supabase=mock_supabase,
    ))
    assert result["status"] == "sent"
    mock_supabase.table.assert_called_with("notification_logs")
    mock_table.insert.assert_called_once()
    call_args = mock_table.insert.call_args[0][0]
    assert call_args["booking_id"] == "b-123"
    assert call_args["type"] == "BOOKING_CONFIRMATION"
    assert call_args["recipient"] == "user@example.com"
    assert call_args["status"] == "SENT"
    assert call_args["details"]["subject"] == "Booking Receipt"


def test_send_booking_confirmation():
    result = asyncio.run(send_booking_confirmation(
        passenger_name="Alice Smith",
        passenger_email="alice@example.com",
        pnr="AL1234",
        flight_number="PK-202",
        fare_class="FLEXIBLE",
        fare_paid_cents=45000,
    ))
    assert result["status"] == "sent"
    assert result["recipient"] == "alice@example.com"
    assert "AL1234" in result["subject"]
    assert "PK-202" in result["subject"]


def test_send_cancellation_receipt():
    result = asyncio.run(send_cancellation_receipt(
        passenger_email="bob@example.com",
        pnr="BOB999",
        refund_amount_cents=30000,
        refund_type="CASH",
    ))
    assert result["status"] == "sent"
    assert result["recipient"] == "bob@example.com"
    assert "BOB999" in result["subject"]


def test_send_approved_rag_response():
    result = asyncio.run(send_approved_rag_response(
        customer_email="carol@example.com",
        pnr="CRL555",
        response_text="Your baggage policy is 2 checked bags.",
    ))
    assert result["status"] == "sent"
    assert result["recipient"] == "carol@example.com"
    assert "CRL555" in result["subject"]


def test_support_approve_triggers_email():
    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_update = MagicMock()
    mock_eq = MagicMock()
    mock_eq.execute = AsyncMock(return_value=MagicMock(data=[{
        "id": "draft-1",
        "customer_email": "passenger@example.com",
        "pnr": "PNR777",
        "draft_response": "Draft text approved by agent",
        "status": "APPROVED",
    }]))
    mock_update.eq.return_value = mock_eq
    mock_table.update.return_value = mock_update
    mock_supabase.table.return_value = mock_table

    with patch("app.utils.email.send_approved_rag_response", new_callable=AsyncMock) as mock_send:
        res = asyncio.run(support_approve(
            req=ApprovalRequest(id="draft-1", action="approve"),
            supabase=mock_supabase,
        ))
        assert res == {"status": "success", "draft_status": "APPROVED"}
        mock_send.assert_awaited_once_with(
            customer_email="passenger@example.com",
            pnr="PNR777",
            response_text="Draft text approved by agent",
            supabase=mock_supabase,
        )
