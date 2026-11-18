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
   disturbing any worker if not. A claim is live unless its owner is
   *confirmed* terminated: each worker holds a liveness advisory lock for its
   whole lifetime (``WorkerLiveness``), which Postgres drops when the worker's
   process or connection ends. Claim age and transaction status are never
   used to decide: an expired lease doesn't stop the task that holds it.
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
import zlib
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


# Advisory-lock namespace for worker liveness locks ("GLWK" in ASCII).
WORKER_LIVENESS_LOCK_NAMESPACE = 0x474C574B


def worker_liveness_key(worker_id: str) -> int:
    """32-bit signed lock key for ``worker_id`` (within the liveness namespace).

    A collision can only make a terminated worker look alive, which errs on
    the safe side (the restore waits/aborts instead of ignoring a claim).
    """
    key = zlib.crc32(worker_id.encode())
    return key - (1 << 32) if key >= (1 << 31) else key


class WorkerLiveness:
    """Worker side: proof of life that Postgres itself revokes on termination.

    The worker holds a session-level advisory lock keyed by its worker_id on a
    dedicated connection for as long as it runs. When the process dies (or its
    connection does) Postgres releases the lock, so the RPC can *confirm* that
    a claim's owner is gone instead of guessing from claim age. All methods are
    blocking; call them from a thread.
    """

    def __init__(self, engine: Engine, worker_id: str):
        self.engine = engine
        self.worker_id = worker_id
        self.key = worker_liveness_key(worker_id)
        self.conn: Optional[Connection] = None

    def acquire(self) -> bool:
        """Take the liveness lock on a fresh connection. False if not acquired."""
        self.close()
        conn = self.engine.connect()
        try:
            acquired = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:ns, :key)"),
                    {"ns": WORKER_LIVENESS_LOCK_NAMESPACE, "key": self.key},
                ).scalar()
            )
            conn.commit()
        except Exception:
            conn.invalidate()
            conn.close()
            raise
        if not acquired:
            conn.close()
            return False
        self.conn = conn
        return True

    def held(self) -> bool:
        """True while our connection is alive and still holds the lock."""
        if self.conn is None:
            return False
        try:
            held = bool(
                self.conn.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks"
                        " WHERE locktype = 'advisory' AND granted"
                        " AND pid = pg_backend_pid()"
                        " AND classid = CAST(:ns AS oid)"
                        " AND objid = CAST(CAST(:key AS bigint) & 4294967295 AS oid)"
                        " AND objsubid = 2)"
                    ),
                    {"ns": WORKER_LIVENESS_LOCK_NAMESPACE, "key": self.key},
                ).scalar()
            )
            self.conn.commit()
            return held
        except Exception as e:
            logger.error(f"Worker {self.worker_id} liveness connection lost: {e}")
            return False

    def close(self) -> None:
        """Drop the liveness connection (Postgres releases the lock with it)."""
        conn, self.conn = self.conn, None
        if conn is None:
            return
        try:
            conn.invalidate()  # never return a lock-holding connection to the pool
        finally:
            conn.close()


def worker_terminated(session: Session, worker_id: str) -> bool:
    """True if ``worker_id`` is confirmed gone: nobody holds its liveness lock.

    Probes by taking the lock and immediately releasing it in one statement,
    so it never stays held by the RPC.
    """
    return bool(
        session.execute(
            text(
                "SELECT CASE WHEN pg_try_advisory_lock(:ns, :key)"
                " THEN pg_advisory_unlock(:ns, :key) ELSE false END"
            ),
            {
                "ns": WORKER_LIVENESS_LOCK_NAMESPACE,
                "key": worker_liveness_key(worker_id),
            },
        ).scalar()
    )


def claimed_transactions(session: Session) -> list[tuple[str, Optional[str]]]:
    """(hash, worker_id) of every claim a running task may still act on.

    Every claim counts — whatever its age or the transaction's status: an
    expired lease does not stop the task that holds it, and a finalizing task
    still holds its claim after the status turns FINALIZED. A claim is only
    left out when its owner is confirmed terminated (its liveness lock is
    free, see ``WorkerLiveness``) or when it has no owner at all: every claim
    query sets ``worker_id`` together with ``blocked_at``, so an ownerless
    claim is not held by any task. The restore rewrites those rows anyway.
    """
    rows = session.execute(
        text(
            "SELECT hash, worker_id FROM transactions"
            " WHERE worker_id IS NOT NULL OR blocked_at IS NOT NULL"
        )
    ).fetchall()
    owners = {row[1] for row in rows if row[1] is not None}
    terminated = {w for w in owners if worker_terminated(session, w)}
    # End the read transaction so the next poll sees fresh data.
    session.rollback()
    ignored = [(h, w) for h, w in rows if w is None or w in terminated]
    if ignored:
        logger.info(
            f"Ignoring {len(ignored)} claim(s) whose owner is confirmed "
            f"terminated or absent: {_describe(ignored)}"
        )
    return [(h, w) for h, w in rows if w is not None and w not in terminated]


def _describe(claimed: list[tuple[str, Optional[str]]]) -> str:
    return ", ".join(f"{h} ({w or 'no worker'})" for h, w in claimed[:5])


async def ensure_claims_releasable(session: Session) -> None:
    """Fail before any worker is disturbed if a live claim has nobody to release it.

    A live claim's owner is a running worker (its liveness lock is held) and
    must be one of the workers subscribed to the control channel; with fewer
    subscribers than distinct live owners, some running owner can't be asked.
    """
    claimed = await run_blocking(claimed_transactions, session)
    if not claimed:
        return
    owners = {worker_id for _, worker_id in claimed}
    subscribers = await worker_control_subscribers()
    if subscribers is None or len(owners) > subscribers:
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


async def maintain_liveness(worker, liveness: WorkerLiveness, interval: float = 5.0):
    """Worker side: hold the liveness lock for as long as the worker runs.

    The worker must not claim while it doesn't hold the lock (a restore would
    treat its claims as abandoned), so ``worker.liveness_ok`` gates claiming.
    If the lock is lost, claiming stops at once and in-flight work is dropped
    before the lock is re-acquired.
    """
    while True:
        if worker.liveness_ok and not await asyncio.to_thread(liveness.held):
            worker.liveness_ok = False
            logger.error(
                f"Worker {worker.worker_id} lost its liveness lock; dropping "
                "in-flight work before re-acquiring it"
            )
            await worker.release_in_flight()
            await asyncio.to_thread(liveness.close)
        if not worker.liveness_ok:
            try:
                acquired = await asyncio.to_thread(liveness.acquire)
            except Exception as e:
                acquired = False
                logger.error(f"Worker {worker.worker_id} liveness lock failed: {e}")
            if acquired:
                worker.liveness_ok = True
                logger.info(f"Worker {worker.worker_id} holds its liveness lock")
            else:
                logger.warning(
                    f"Worker {worker.worker_id} could not take its liveness lock; "
                    "not claiming work until it can"
                )
        await asyncio.sleep(interval)
