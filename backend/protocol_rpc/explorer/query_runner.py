"""Bound Explorer reads and close their sessions before returning to the API loop."""

import threading
import time
from typing import Any, Callable

from fastapi import HTTPException

from backend.database_handler.session_factory import DatabaseSessionManager

from . import queries


class ExplorerQueryRunner:
    def __init__(
        self,
        db_manager: DatabaseSessionManager,
        *,
        max_concurrent: int = 2,
        counts_ttl: float = 5.0,
    ) -> None:
        self._db_manager = db_manager
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._counts_ttl = counts_ttl
        self._counts_lock = threading.Lock()
        self._cached_counts: tuple[float, dict] | None = None

    @staticmethod
    def _busy() -> HTTPException:
        return HTTPException(
            status_code=503,
            detail="Explorer busy; retry shortly",
            headers={"Retry-After": "1"},
        )

    def run(self, query: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if not self._slots.acquire(blocking=False):
            raise self._busy()
        try:
            # Query functions materialize their results. Closing here rolls back
            # the read transaction in the same worker, without an event-loop hop.
            with self._db_manager.open_session() as session:
                return query(session, *args, **kwargs)
        finally:
            self._slots.release()

    def counts(self) -> dict:
        cached = self._cached_counts
        if cached is not None and cached[0] > time.monotonic():
            return dict(cached[1])

        # Coalesce refreshes across Explorer server renders. A concurrent caller
        # can use the previous counts while the single refresh is in progress.
        if not self._counts_lock.acquire(blocking=False):
            if cached is not None:
                return dict(cached[1])
            raise self._busy()
        try:
            cached = self._cached_counts
            if cached is not None and cached[0] > time.monotonic():
                return dict(cached[1])
            counts = self.run(queries.get_stats_counts)
            self._cached_counts = (
                time.monotonic() + self._counts_ttl,
                dict(counts),
            )
            return counts
        finally:
            self._counts_lock.release()
