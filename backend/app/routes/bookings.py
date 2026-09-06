import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from supabase import AsyncClient

logger = logging.getLogger("app.routes.bookings")

from app.dependencies import get_supabase_client, verify_idempotency_key
from app.models.requests import BookingConfirmRequest, SeatHoldRequest, CancellationRequest
from app.models.responses import (
    BookingResponse,
    CancellationResponse,
    HoldResponse,
    PartialCancellationResponse,
    TravelCreditResponse,
)
from app.services.bookings import (
    confirm_booking_service,
    get_booking_by_pnr,
    hold_seat_service,
)
from app.utils.idempotency import get_idempotency_manager


class FlexiblePrefix(str):
    """Enables router prefix to match both '/bookings' and '/api/v1/bookings' seamlessly."""

    def __eq__(self, other: Any) -> bool:
        if str(self) == other:
            return True
        if other in ("/bookings", "/api/v1/bookings"):
            return True
        return False


router = APIRouter(prefix=FlexiblePrefix("/bookings"), tags=["Bookings"])


@router.get("/", summary="Check bookings router readiness")
async def get_bookings_status() -> dict[str, str]:
    """Check bookings router readiness."""
    return {"status": "operational", "module": "bookings"}


@router.post(
    "/hold",
    response_model=HoldResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Establish an atomic temporary seat reservation hold",
)
async def hold_seat(
    payload: SeatHoldRequest,
    idempotency_key: str = Depends(verify_idempotency_key),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> HoldResponse:
    """Acquire an atomic row-lock and reserve a seat for a TTL window."""
    # 1. Check idempotency cache to avoid duplicate hold creation on retries
    if idempotency_key:
        cached_record = await get_idempotency_manager().get_record(
            key=idempotency_key,
            supabase_client=supabase,
        )
        if cached_record and cached_record.response_data:
            return HoldResponse(**cached_record.response_data)

    # 2. Call hold_seat_service
    hold_data = await hold_seat_service(
        supabase=supabase,
        flight_id=str(payload.flight_id),
        seat_id=str(payload.seat_id),
        session_id=payload.session_id,
        hold_minutes=payload.hold_minutes,
    )

    response = HoldResponse(**hold_data)
    response_dict = response.model_dump(mode="json")

    # 3. Store idempotency record
    if idempotency_key:
        try:
            await (
                supabase.table("idempotency_records")
                .upsert(
                    {
                        "key": idempotency_key,
                        "endpoint": "/api/v1/bookings/hold",
                        "response_code": 201,
                        "status_code": 201,
                        "response_body": response_dict,
                        "response_data": response_dict,
                    }
                )
                .execute()
            )
        except Exception as exc:
            logger.warning("Failed to store hold record in idempotency_records: %s", exc)

        await get_idempotency_manager().save_record(
            key=idempotency_key,
            status_code=201,
            response_data=response_dict,
            supabase_client=supabase,
        )

    return response


@router.post(
    "/confirm",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm a booking from a held seat idempotently",
)
async def confirm_booking(
    payload: BookingConfirmRequest,
    request: Request,
    idempotency_key: str = Depends(verify_idempotency_key),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> BookingResponse:
    """Validate price lock, call confirm_booking RPC, and persist idempotency ledger."""
    # Extract client IP address from proxy headers or connection socket
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (request.client.host if request.client else "127.0.0.1")
    )

    booking_data = await confirm_booking_service(
        supabase=supabase,
        payload=payload,
        idempotency_key=idempotency_key,
        client_ip=client_ip,
    )

    return BookingResponse(**booking_data)


@router.get(
    "/{pnr}",
    status_code=status.HTTP_200_OK,
    summary="Lookup booking and flight details by PNR",
)
async def get_booking(
    pnr: str,
    supabase: AsyncClient = Depends(get_supabase_client),
) -> dict[str, Any]:
    """Retrieve full booking, flight, and passenger manifest details by PNR."""
    return await get_booking_by_pnr(supabase=supabase, pnr=pnr)


@router.post(
    "/{pnr}/cancel",
    response_model=CancellationResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel a booking by PNR",
)
async def cancel_booking(
    pnr: str,
    supabase: AsyncClient = Depends(get_supabase_client),
    idempotency_key: str = Depends(verify_idempotency_key),
) -> CancellationResponse:
    from app.services.bookings import cancel_booking_service
    res = await cancel_booking_service(supabase=supabase, pnr=pnr)
    return CancellationResponse(**res)


@router.post(
    "/{pnr}/cancel-passenger",
    response_model=PartialCancellationResponse,
    status_code=status.HTTP_200_OK,
    summary="Partially cancel a group booking for specified passengers",
)
async def cancel_passenger_partial(
    pnr: str,
    payload: CancellationRequest,
    supabase: AsyncClient = Depends(get_supabase_client),
    idempotency_key: str = Depends(verify_idempotency_key),
) -> dict[str, Any]:
    from app.services.bookings import cancel_passenger_partial_service
    if not payload.passenger_ids:
        raise HTTPException(status_code=400, detail="Passenger IDs must be provided for partial cancellation")
    res = await cancel_passenger_partial_service(
        supabase=supabase,
        parent_pnr=pnr,
        passenger_ids=[str(pid) for pid in payload.passenger_ids]
    )
    return res


@router.post(
    "/{pnr}/travel-credit",
    response_model=TravelCreditResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cancel a booking and issue a travel credit voucher",
)
async def issue_travel_credit(
    pnr: str,
    supabase: AsyncClient = Depends(get_supabase_client),
    idempotency_key: str = Depends(verify_idempotency_key),
) -> dict[str, Any]:
    from app.services.bookings import issue_travel_credit_service
    return await issue_travel_credit_service(supabase=supabase, pnr=pnr)

