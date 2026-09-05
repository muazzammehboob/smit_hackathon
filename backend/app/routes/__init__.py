"""Routes package consolidating all endpoint routers."""

from app.routes.health import router as health_router
from app.routes.admin import router as admin_router
from app.routes.search import router as search_router
from app.routes.bookings import router as bookings_router
from app.routes.waitlist import router as waitlist_router
from app.routes.webhooks import router as webhooks_router

__all__ = [
    "health_router",
    "admin_router",
    "search_router",
    "bookings_router",
    "waitlist_router",
    "webhooks_router",
]
