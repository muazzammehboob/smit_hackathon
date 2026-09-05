"""Idempotency management utilities for idempotent request handling."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class IdempotencyRecord:
    """Representation of a cached idempotent request and response."""

    key: str
    status_code: int
    response_data: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class IdempotencyManager:
    """Thread-safe idempotency manager supporting in-memory cache and Supabase persistence."""

    def __init__(self) -> None:
        self._memory_cache: dict[str, IdempotencyRecord] = {}
        self._lock = asyncio.Lock()

    async def get_record(
        self, key: str, supabase_client: Optional[Any] = None
    ) -> Optional[IdempotencyRecord]:
        """Retrieve an idempotency record by key, checking memory first then Supabase."""
        async with self._lock:
            if key in self._memory_cache:
                return self._memory_cache[key]

        if supabase_client is not None:
            try:
                response = (
                    await supabase_client.table("idempotency_records")
                    .select("*")
                    .eq("key", key)
                    .maybe_single()
                    .execute()
                )
                if response and response.data:
                    data = response.data
                    record = IdempotencyRecord(
                        key=data.get("key", key),
                        status_code=data.get("status_code", 200),
                        response_data=data.get("response_data", {}),
                    )
                    async with self._lock:
                        self._memory_cache[key] = record
                    return record
            except Exception:
                return None

        return None

    async def save_record(
        self,
        key: str,
        status_code: int,
        response_data: dict[str, Any],
        supabase_client: Optional[Any] = None,
    ) -> IdempotencyRecord:
        """Store an idempotency record in both memory cache and Supabase."""
        record = IdempotencyRecord(
            key=key,
            status_code=status_code,
            response_data=response_data,
            created_at=datetime.now(timezone.utc),
        )

        async with self._lock:
            self._memory_cache[key] = record

        if supabase_client is not None:
            try:
                await (
                    supabase_client.table("idempotency_records")
                    .upsert(
                        {
                            "key": key,
                            "status_code": status_code,
                            "response_data": response_data,
                            "created_at": record.created_at.isoformat(),
                        }
                    )
                    .execute()
                )
            except Exception:
                return record

        return record

    async def clear_memory_cache(self) -> None:
        """Clear all in-memory records (useful for test suites)."""
        async with self._lock:
            self._memory_cache.clear()


_idempotency_manager_instance = IdempotencyManager()


def get_idempotency_manager() -> IdempotencyManager:
    """Return singleton instance of IdempotencyManager."""
    return _idempotency_manager_instance


async def check_idempotency_key(
    key: str, supabase_client: Optional[Any] = None
) -> Optional[IdempotencyRecord]:
    """Helper function to look up an idempotency key."""
    return await _idempotency_manager_instance.get_record(key, supabase_client)


async def record_idempotency(
    key: str,
    status_code: int,
    response_data: dict[str, Any],
    supabase_client: Optional[Any] = None,
) -> IdempotencyRecord:
    """Helper function to record a response for an idempotency key."""
    return await _idempotency_manager_instance.save_record(
        key, status_code, response_data, supabase_client
    )
