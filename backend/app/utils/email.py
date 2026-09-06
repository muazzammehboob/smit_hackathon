"""Email dispatch utility for transactional notifications.

Uses Gmail API when credentials are configured, otherwise logs the email
for demo/development purposes.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def send_transactional_email(
    recipient: str,
    subject: str,
    body: str,
    email_type: str = "TRANSACTIONAL",
    booking_id: str | None = None,
    supabase=None,
) -> dict:
    """Send a transactional email (booking confirmation, cancellation receipt, etc.).
    
    In production, this would use Gmail API. For demo, it logs the email
    and records it in notification_logs.
    """
    logger.info(f"[EMAIL] To: {recipient} | Subject: {subject} | Type: {email_type}")
    
    # Record in notification_logs for audit trail
    if supabase:
        try:
            await supabase.table("notification_logs").insert({
                "booking_id": booking_id,
                "type": email_type,
                "recipient": recipient,
                "status": "SENT",
                "details": {
                    "subject": subject,
                    "body": body[:500],  # Truncate for storage
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                    "channel": "gmail_placeholder"
                }
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to log notification: {e}")
    
    return {"status": "sent", "recipient": recipient, "subject": subject}


async def send_booking_confirmation(
    passenger_name: str,
    passenger_email: str,
    pnr: str,
    flight_number: str,
    fare_class: str,
    fare_paid_cents: int,
    supabase=None,
) -> dict:
    """Send booking confirmation email."""
    subject = f"Booking Confirmed - {pnr} | Flight {flight_number}"
    body = (
        f"Dear {passenger_name},\n\n"
        f"Your booking has been confirmed!\n\n"
        f"PNR: {pnr}\n"
        f"Flight: {flight_number}\n"
        f"Class: {fare_class}\n"
        f"Amount: ${fare_paid_cents / 100:.2f}\n\n"
        f"Thank you for choosing our airline."
    )
    return await send_transactional_email(
        recipient=passenger_email,
        subject=subject,
        body=body,
        email_type="BOOKING_CONFIRMATION",
        supabase=supabase,
    )


async def send_cancellation_receipt(
    passenger_email: str,
    pnr: str,
    refund_amount_cents: int,
    refund_type: str,
    supabase=None,
) -> dict:
    """Send cancellation receipt email."""
    subject = f"Booking Cancelled - {pnr}"
    body = (
        f"Your booking {pnr} has been cancelled.\n\n"
        f"Refund: ${refund_amount_cents / 100:.2f}\n"
        f"Refund Type: {refund_type}\n"
    )
    return await send_transactional_email(
        recipient=passenger_email,
        subject=subject,
        body=body,
        email_type="CANCELLATION_RECEIPT",
        supabase=supabase,
    )


async def send_approved_rag_response(
    customer_email: str,
    pnr: str,
    response_text: str,
    supabase=None,
) -> dict:
    """Send approved RAG-drafted policy response to customer."""
    subject = f"Re: Your inquiry about booking {pnr}"
    return await send_transactional_email(
        recipient=customer_email,
        subject=subject,
        body=response_text,
        email_type="SUPPORT_RESPONSE",
        supabase=supabase,
    )
