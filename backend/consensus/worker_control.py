"""
Coordination between RPC instances and consensus workers for operations that
rewrite the ``transactions`` table out from under the workers (snapshot
restore).

Workers keep claimed transactions in memory while they process them; if the
table is rewritten meanwhile, the worker later writes stale results back on
top of the restored state. The protocol is:

1. RPC takes the **restore gate**: an exclusive, session-level Postgres
   advisory lock held on a dedicated connection for the whole restore.
   Every worker claim (and every stuck-tx recovery write) first takes the
   *shared* form of the same lock inside its own DB transaction and skips the
   claim if it can't. While the gate is held no worker — including one that
   never received step 2 or that started after it — can claim anything.
   Taking the gate waits for claim transactions already in progress, so their
   claims are visible afterwards.
2. RPC publishes ``RELEASE_IN_FLIGHT_EVENT``: each worker cancels its
   in-flight tasks and clears its claims.
3. RPC waits until no transaction is claimed. Because of the gate this is a
   real barrier: zero claims means no worker is processing anything. If claims
   remain after the timeout the restore is aborted and the DB left untouched.
4. RPC rewrites the database, then releases the gate. If the RPC process
   dies, Postgres drops the lock with its connection.
"""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from loguru import logger
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

WORKER_CONTROL_CHANNEL = "worker:control"
RELEASE_IN_FLIGHT_EVENT = "release_in_flight"

# Arbitrary application-wide advisory lock key ("GLRESTOR" in ASCII).
RESTORE_GATE_LOCK_KEY = 0x474C524553544F52

DEFAULT_RELEASE_TIMEOUT_SECONDS = 30.0


class WorkerCoordinationError(Exception):
    """Workers could not be brought to a safe state; nothing was changed."""


def claim_gate_open(session: Session) -> bool:
    """Take the shared side of the restore gate for the current DB transaction.

    Returns False while a restore holds the gate; the caller must then not
    claim or modify transactions. The shared lock is released when the
    session's transaction commits or rolls back, so the check must run in the
    same transaction as the write it protects.
    """
    return bool(
        session.execute(
            text("SELECT pg_try_advisory_xact_lock_shared(:key)"),
            {"key": RESTORE_GATE_LOCK_KEY},
        ).scalar()
    )


@asynccontextmanager
async def restore_gate(
    engine: Engine,
    timeout_seconds: float = DEFAULT_RELEASE_TIMEOUT_SECONDS,
    poll_interval: float = 0.1,
) -> AsyncIterator[None]:
    """Hold the exclusive restore gate, blocking all worker claims."""
    conn = engine.connect()
    acquired = False
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            acquired = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": RESTORE_GATE_LOCK_KEY},
                ).scalar()
            )
            conn.commit()
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise WorkerCoordinationError(
                    "could not acquire the restore gate (another restore in "
                    "progress, or a claim transaction is stuck)"
                )
            await asyncio.sleep(poll_interval)
        yield
    finally:
        if acquired:
            try:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": RESTORE_GATE_LOCK_KEY},
                )
                conn.commit()
            except Exception as e:
                # Never return a pooled connection that still holds the lock:
                # dropping the physical connection releases it.
                logger.error(f"Failed to release restore gate: {e}")
                conn.invalidate()
        conn.close()


async def publish_worker_control(
    event: str, data: Optional[dict] = None, redis_url: Optional[str] = None
) -> Optional[int]:
    """Publish a control event to all consensus workers.

    Returns the number of subscribers that received it, or None when Redis is
    not configured / reachable.
    """
    redis_url = redis_url or os.environ.get("REDIS_URL")
    if not redis_url:
        logger.warning(
            f"REDIS_URL not set; cannot publish worker control event '{event}'"
        )
        return None

    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    try:
        subscribers = await client.publish(
            WORKER_CONTROL_CHANNEL, json.dumps({"event": event, "data": data or {}})
        )
        logger.info(
            f"Published worker control event '{event}' to {subscribers} subscribers"
        )
        return subscribers
    except Exception as e:
        logger.error(f"Failed to publish worker control event '{event}': {e}")
        return None
    finally:
        await client.close()


def claimed_transactions(session: Session) -> list[tuple[str, Optional[str]]]:
    """(hash, worker_id) of every transaction currently claimed by a worker."""
    rows = session.execute(
        text(
            "SELECT hash, worker_id FROM transactions "
            "WHERE worker_id IS NOT NULL OR blocked_at IS NOT NULL"
        )
    ).fetchall()
    # End the read transaction so the next poll sees fresh data.
    session.rollback()
    return [(row[0], row[1]) for row in rows]


async def wait_for_claims_released(
    session: Session,
    timeout_seconds: float = DEFAULT_RELEASE_TIMEOUT_SECONDS,
    poll_interval: float = 0.25,
) -> None:
    """Wait until no transaction is claimed by a worker.

    Raises WorkerCoordinationError if claims remain after ``timeout_seconds``.
    Only meaningful while the restore gate is held (otherwise new claims can
    appear right after this returns).
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        claimed = claimed_transactions(session)
        if not claimed:
            return
        if time.monotonic() >= deadline:
            sample = ", ".join(f"{h} ({w or 'no worker'})" for h, w in claimed[:5])
            raise WorkerCoordinationError(
                f"{len(claimed)} transaction(s) still claimed by workers after "
                f"{timeout_seconds}s: {sample}"
            )
        await asyncio.sleep(poll_interval)
