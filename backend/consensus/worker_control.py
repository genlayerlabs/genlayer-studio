"""
Control channel between RPC instances and consensus workers.

Used to coordinate operations that rewrite the ``transactions`` table out from
under the workers (e.g. restoring a snapshot). Workers keep claimed
transactions in memory while they process them; if the table is rewritten
while that happens, the worker later writes stale results back on top of the
restored state. The protocol is:

1. RPC publishes ``PAUSE_EVENT``: every worker stops claiming new work,
   cancels its in-flight tasks and clears its claims in the database.
2. RPC waits until no transaction is claimed (``worker_id``/``blocked_at`` set).
3. RPC rewrites the database.
4. RPC publishes ``RESUME_EVENT``: workers start claiming again.

Workers auto-resume after ``pause_seconds`` so a crashed RPC call cannot leave
consensus paused forever.
"""

import asyncio
import json
import os
import time
from typing import Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

WORKER_CONTROL_CHANNEL = "worker:control"
PAUSE_EVENT = "pause_and_release"
RESUME_EVENT = "resume"

DEFAULT_PAUSE_SECONDS = 120.0
DEFAULT_RELEASE_TIMEOUT_SECONDS = 30.0


async def publish_worker_control(
    event: str, data: Optional[dict] = None, redis_url: Optional[str] = None
) -> Optional[int]:
    """Publish a control event to all consensus workers.

    Returns the number of subscribers that received it, or None when Redis is
    not configured / reachable (no workers to coordinate with).
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


def count_claimed_transactions(session: Session) -> int:
    """Number of transactions currently claimed by a worker."""
    result = session.execute(
        text(
            "SELECT COUNT(*) FROM transactions "
            "WHERE worker_id IS NOT NULL OR blocked_at IS NOT NULL"
        )
    ).scalar()
    # End the read transaction so the next poll sees fresh data.
    session.rollback()
    return int(result or 0)


async def wait_for_claims_released(
    session: Session,
    timeout_seconds: float = DEFAULT_RELEASE_TIMEOUT_SECONDS,
    poll_interval: float = 0.25,
) -> bool:
    """Wait until no transaction is claimed by a worker.

    Returns True if all claims were released before ``timeout_seconds``.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = count_claimed_transactions(session)
        if remaining == 0:
            return True
        if time.monotonic() >= deadline:
            logger.warning(
                f"{remaining} transaction(s) still claimed by workers after "
                f"{timeout_seconds}s; proceeding anyway"
            )
            return False
        await asyncio.sleep(poll_interval)
