"""Main FastAPI application entrypoint with lifespan, CORS, error handling, and routing."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import logging
from app.config import settings
from app.dependencies import close_supabase_client, get_supabase_client
from app.routes import (
    admin_router,
    bookings_router,
    health_router,
    search_router,
    waitlist_router,
    webhooks_router,
)

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager controlling startup initialization and shutdown teardown."""
    # Startup phase: test database connectivity
    try:
        supabase = await get_supabase_client()
        await supabase.table("flights").select("id").limit(1).execute()
        logger.info("Database connectivity check succeeded on startup.")
    except Exception as exc:
        logger.warning("Database connectivity check failed on startup: %s", exc)

    yield

    # Shutdown phase
    await close_supabase_client()


app = FastAPI(
    title="Flight Management System API",
    description="Backend API for flight booking, seat inventory, and RAG knowledge base",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
    ],
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Standardized Exception Handlers
@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Standardize HTTP and routing exception responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": str(exc.detail),
            "error_code": f"HTTP_{exc.status_code}",
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Standardize FastAPI HTTP exception responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": str(exc.detail),
            "error_code": f"HTTP_{exc.status_code}",
        },
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Standardize validation error responses."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": str(exc),
            "error_code": "VALIDATION_ERROR",
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all standardized handler for unexpected exceptions."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected internal server error occurred",
            "error_code": "INTERNAL_SERVER_ERROR",
        },
    )


@app.get("/")
async def root():
    """Root endpoint returning service metadata."""
    return {
        "status": "online",
        "online": True,
        "service": "flight management",
        "version": app.version,
        "docs": "/docs",
        "health": "ok"
    }


# Health router included at root for load balancers and orchestrators
app.include_router(health_router)

# Feature routers included under /api/v1
app.include_router(health_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(search_router, prefix="/api/v1")
app.include_router(bookings_router, prefix="/api/v1")
app.include_router(waitlist_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
