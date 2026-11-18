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
   never received step 3 or that started after it — can claim anything.
   Taking the gate waits for claim transactions already in progress, so their
   claims are visible afterwards.
2. RPC checks that every live claim (see ``claimed_transactions``) can be
   released by a worker listening on the control channel, and aborts before
   disturbing any worker if not.
3. RPC publishes ``RELEASE_IN_FLIGHT_EVENT``: each worker cancels its
   in-flight tasks and clears its claims.
4. RPC waits until no live claim remains. Because of the gate this is a real
   barrier: zero live claims means no worker is processing anything. If live
   claims remain after the timeout the restore is aborted without touching
   the snapshot; work the workers cancelled in step 3 stays cancelled (its
   transactions were released and get processed again).
5. RPC rewrites the database, then releases the gate. If the RPC process
   dies, Postgres drops the lock with its connection.

All DB work runs in worker threads so the RPC event loop is never blocked.
"""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional, TypeVar

from loguru import logger
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

WORKER_CONTROL_CHANNEL = "worker:control"
RELEASE_IN_FLIGHT_EVENT = "release_in_flight"

# Arbitrary application-wide advisory lock key ("GLRESTOR" in ASCII).
RESTORE_GATE_LOCK_KEY = 0x474C524553544F52

DEFAULT_RELEASE_TIMEOUT_SECONDS = 30.0

T = TypeVar("T")


class WorkerCoordinationError(Exception):
    """Workers could not be brought to a safe state; the snapshot was not restored."""


async def run_blocking(fn: Callable[..., T], *args: Any) -> T:
    """Run blocking ``fn`` in a thread; if cancelled, still wait for it to end.

    The thread can't be interrupted, so returning early would let the caller
    reuse its connection/session (or release the gate) while it still runs.
    """
    future = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        await asyncio.wait({future})
        raise


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
    conn = await run_blocking(engine.connect)
    acquired = False

    def try_acquire() -> None:
        # Set from the thread so it stays accurate even if the await is cancelled.
        nonlocal acquired
        acquired = bool(
            conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": RESTORE_GATE_LOCK_KEY},
            ).scalar()
        )
        conn.commit()

    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            await run_blocking(try_acquire)
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
        await run_blocking(_release_gate, conn, acquired)


def _release_gate(conn: Connection, acquired: bool) -> None:
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


async def worker_control_subscribers(
    redis_url: Optional[str] = None,
) -> Optional[int]:
    """Number of workers listening for control events, or None if unknown."""
    redis_url = redis_url or os.environ.get("REDIS_URL")
    if not redis_url:
        return None

    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    try:
        counts = await client.pubsub_numsub(WORKER_CONTROL_CHANNEL)
        return int(counts[0][1]) if counts else 0
    except Exception as e:
        logger.error(f"Failed to count worker control subscribers: {e}")
        return None
    finally:
        await client.close()


def claim_lease_minutes() -> int:
    """Claim lease after which worker recovery treats a claim as abandoned."""
    return int(os.environ.get("TRANSACTION_TIMEOUT_MINUTES", "30"))


def claimed_transactions(
    session: Session, lease_minutes: Optional[int] = None
) -> list[tuple[str, Optional[str]]]:
    """(hash, worker_id) of every live claim, i.e. one a worker may still act on.

    Like ConsensusWorker.recover_stuck_transactions, terminal transactions and
    claims whose lease and consensus progress are both past the recovery
    timeout don't count: no running worker answers for them.
    """
    rows = session.execute(
        text(
            """
            SELECT hash, worker_id FROM transactions
            WHERE blocked_at IS NOT NULL
              AND status NOT IN ('FINALIZED', 'CANCELED')
              AND (
                  blocked_at >= NOW() - CAST(:lease AS INTERVAL)
                  OR (
                      SELECT to_timestamp(MAX(progress.value::double precision))
                      FROM jsonb_each_text(
                          COALESCE(
                              transactions.consensus_history->'current_monitoring',
                              '{}'::jsonb
                          )
                      ) AS progress(key, value)
                      WHERE progress.value ~ '^[0-9]+(\\.[0-9]+)?$'
                  ) >= NOW() - CAST(:lease AS INTERVAL)
              )
            """
        ),
        {"lease": f"{lease_minutes or claim_lease_minutes()} minutes"},
    ).fetchall()
    # End the read transaction so the next poll sees fresh data.
    session.rollback()
    return [(row[0], row[1]) for row in rows]


def _describe(claimed: list[tuple[str, Optional[str]]]) -> str:
    return ", ".join(f"{h} ({w or 'no worker'})" for h, w in claimed[:5])


async def ensure_claims_releasable(session: Session) -> None:
    """Fail before any worker is disturbed if a live claim has nobody to release it.

    A claim's owner must be one of the workers subscribed to the control
    channel; with fewer subscribers than distinct owners, some owner is gone.
    """
    claimed = await run_blocking(claimed_transactions, session)
    if not claimed:
        return
    owners = {worker_id for _, worker_id in claimed}
    subscribers = await worker_control_subscribers()
    if None in owners or subscribers is None or len(owners) > subscribers:
        raise WorkerCoordinationError(
            f"{len(claimed)} transaction(s) claimed by {len(owners)} worker(s) but "
            f"{subscribers if subscribers is not None else 'no'} worker(s) can be "
            f"asked to release them: {_describe(claimed)}"
        )


async def wait_for_claims_released(
    session: Session,
    timeout_seconds: float = DEFAULT_RELEASE_TIMEOUT_SECONDS,
    poll_interval: float = 0.25,
) -> None:
    """Wait until no live claim remains.

    Raises WorkerCoordinationError if claims remain after ``timeout_seconds``.
    Only meaningful while the restore gate is held (otherwise new claims can
    appear right after this returns).
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        claimed = await run_blocking(claimed_transactions, session)
        if not claimed:
            return
        if time.monotonic() >= deadline:
            raise WorkerCoordinationError(
                f"{len(claimed)} transaction(s) still claimed by workers after "
                f"{timeout_seconds}s: {_describe(claimed)}"
            )
        await asyncio.sleep(poll_interval)
