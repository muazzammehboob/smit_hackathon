"""Waitlist management and priority queueing endpoints."""

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from supabase import AsyncClient

from app.dependencies import get_supabase_client
from app.models.requests import WaitlistJoinRequest
from app.models.responses import WaitlistResponse as BaseWaitlistResponse
from app.services.waitlist import (
    claim_waitlist_seat_service,
    get_waitlist_position_service,
    join_waitlist_service,
    leave_waitlist_service,
)

logger = logging.getLogger("app.routes.waitlist")


class FlexiblePrefix(str):
    """Enables router prefix to match both '/waitlist' and '/api/v1/waitlist' seamlessly."""

    def __eq__(self, other: Any) -> bool:
        if str(self) == other:
            return True
        if other in ("/waitlist", "/api/v1/waitlist"):
            return True
        return False


class WaitlistResponse(BaseWaitlistResponse):
    """Standing waitlist queue registration details including queue position."""

    position: Optional[int] = None


router = APIRouter(prefix=FlexiblePrefix("/waitlist"), tags=["Waitlist"])


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary="Check waitlist router readiness",
)
@router.get(
    "",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def get_waitlist_status() -> dict[str, str]:
    """Check waitlist router readiness."""
    return {"status": "operational", "module": "waitlist"}


@router.post(
    "/join",
    response_model=WaitlistResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register for priority standby waitlist on a full flight",
)
@router.post(
    "",
    response_model=WaitlistResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def join_waitlist(
    payload: WaitlistJoinRequest,
    supabase: AsyncClient = Depends(get_supabase_client),
) -> WaitlistResponse:
    """Enter priority standby waitlist when requested flight class has 0 available seats."""
    data = await join_waitlist_service(supabase=supabase, payload=payload)
    return WaitlistResponse(**data)


@router.get(
    "/position",
    response_model=WaitlistResponse,
    status_code=status.HTTP_200_OK,
    summary="Lookup active waitlist position and priority score",
)
async def get_waitlist_position(
    flight_id: str = Query(..., description="Scheduled flight UUID"),
    email: str = Query(..., description="Passenger contact email"),
    supabase: AsyncClient = Depends(get_supabase_client),
) -> WaitlistResponse:
    """Check current waitlist queue position for a waiting passenger."""
    data = await get_waitlist_position_service(
        supabase=supabase,
        flight_id=flight_id,
        email=email,
    )
    return WaitlistResponse(**data)


@router.delete(
    "/{waitlist_id}",
    status_code=status.HTTP_200_OK,
    summary="Forfeit waitlist position and leave standby queue",
)
async def leave_waitlist(
    waitlist_id: str,
    supabase: AsyncClient = Depends(get_supabase_client),
) -> dict[str, Any]:
    """Remove passenger standby entry from active waitlist."""
    return await leave_waitlist_service(
        supabase=supabase,
        waitlist_id=waitlist_id,
    )


@router.post(
    "/{waitlist_id}/claim",
    status_code=status.HTTP_200_OK,
    summary="Claim promoted waitlist seat and confirm booking",
)
async def claim_waitlist_seat(
    waitlist_id: str,
    request: Request,
    supabase: AsyncClient = Depends(get_supabase_client),
) -> dict[str, Any]:
    """Convert promoted waitlist hold into confirmed booking ticket."""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (request.client.host if request.client else "127.0.0.1")
    )
    return await claim_waitlist_seat_service(
        supabase=supabase,
        waitlist_id=waitlist_id,
        client_ip=client_ip,
    )
