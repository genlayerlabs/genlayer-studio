"""Bound Explorer reads and close their sessions before returning to the API loop."""

import threading
import time
from typing import Any, Callable

from fastapi import HTTPException

from backend.database_handler.session_factory import DatabaseSessionManager

from . import queries


class ExplorerBusyError(HTTPException):
    """Expected load-shedding response; excluded from Sentry error reporting."""

    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            detail="Explorer busy; retry shortly",
            headers={"Retry-After": "1"},
        )


class _CachedQuery:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self.lock = threading.Lock()
        self.value: tuple[float, dict] | None = None

    def fresh(self) -> dict | None:
        value = self.value
        if value is not None and value[0] > time.monotonic():
            return dict(value[1])
        return None


class ExplorerQueryRunner:
    def __init__(
        self,
        db_manager: DatabaseSessionManager,
        *,
        max_concurrent: int = 2,
        max_waiting: int = 8,
        wait_timeout: float = 1.0,
        counts_ttl: float = 5.0,
        stats_ttl: float = 10.0,
    ) -> None:
        self._db_manager = db_manager
        self._slots = threading.BoundedSemaphore(max_concurrent)
        # Caps blocked worker threads so waiting cannot starve the API threadpool.
        self._admission = threading.BoundedSemaphore(max_concurrent + max_waiting)
        self._wait_timeout = wait_timeout
        self._counts = _CachedQuery(counts_ttl)
        self._stats = _CachedQuery(stats_ttl)

    @staticmethod
    def _busy() -> HTTPException:
        return ExplorerBusyError()

    def run(self, query: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if not self._admission.acquire(blocking=False):
            raise self._busy()
        try:
            if not self._slots.acquire(timeout=self._wait_timeout):
                raise self._busy()
            try:
                # Query functions materialize their results. Closing here rolls back
                # the read transaction in the same worker, without an event-loop hop.
                with self._db_manager.open_session() as session:
                    return query(session, *args, **kwargs)
            finally:
                self._slots.release()
        finally:
            self._admission.release()

    def counts(self) -> dict:
        return self._cached(self._counts, queries.get_stats_counts)

    def stats(self) -> dict:
        return self._cached(self._stats, queries.get_stats)

    def _cached(self, cache: _CachedQuery, query: Callable[..., dict]) -> dict:
        fresh = cache.fresh()
        if fresh is not None:
            return fresh

        # Coalesce refreshes across Explorer server renders. A concurrent caller
        # uses the previous result while the single refresh is in progress.
        if not cache.lock.acquire(blocking=False):
            if cache.value is not None:
                return dict(cache.value[1])
            if not cache.lock.acquire(timeout=self._wait_timeout):
                raise self._busy()
        try:
            fresh = cache.fresh()
            if fresh is not None:
                return fresh
            result = self.run(query)
            cache.value = (time.monotonic() + cache.ttl, dict(result))
            return result
        finally:
            cache.lock.release()
