"""
Regression tests for the consensus-health "orphaned transactions" detector.

Old logic: count "blocked txs with no active workers in the last hour".
That's coarse and prone to false positives — e.g. when traffic is bursty,
no NEW txs in the last hour means the active-workers query (filtered by
created_at > now - 1h) returns zero, even though workers are actively
processing OLD txs. The detector then declares all in-flight txs as
"orphaned" and the dashboard alerts DEGRADED for what's actually a
healthy system slowly draining a backlog.

New logic (per builder request):
- A long queue is not a problem if its head is making progress.
- The head being stuck IS a problem regardless of queue depth.

A contract's "head" is the oldest non-final tx for that contract.
The head is "stuck" when:
  - it was created > HEAD_STUCK_AFTER_MINUTES ago, AND
  - no tx for that contract has a fresh blocked_at within the
    RECENT_ACTIVITY_WINDOW_MINUTES window.

`total_orphaned_transactions` now counts the number of CONTRACTS in
this state, not the count of in-flight txs in tail of queues.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker


@dataclass
class _StubManager:
    """Minimal DatabaseSessionManager stand-in: only `engine` is read by
    the health module's _check_consensus_health code path."""

    engine: Engine


@pytest.fixture(autouse=True)
def _wire_health_module_to_test_engine(engine: Engine):
    """Point session_factory's global manager at the test engine for the
    duration of each test, then restore. Also stubs the rpc_router_ref
    truthy check inside _check_consensus_health."""
    from backend.database_handler import session_factory
    from backend.protocol_rpc import health as health_module

    prev_mgr = session_factory._db_manager
    prev_router = health_module._rpc_router_ref
    prev_scan_suppressed_until = health_module._no_progress_scan_suppressed_until

    session_factory._db_manager = _StubManager(engine=engine)
    health_module._rpc_router_ref = object()  # truthy
    health_module._no_progress_scan_suppressed_until = 0.0

    yield

    session_factory._db_manager = prev_mgr
    health_module._rpc_router_ref = prev_router
    health_module._no_progress_scan_suppressed_until = prev_scan_suppressed_until


def _insert_tx(
    session,
    *,
    tx_hash: str,
    to_address: str,
    status: str,
    nonce: int,
    created_at: datetime,
    blocked_at: datetime | None = None,
    worker_id: str | None = None,
    timestamp_awaiting_finalization: int | None = None,
    recovery_count: int = 0,
    consensus_history: dict | None = None,
    consensus_data: dict | None = None,
):
    """Direct INSERT — bypass the processor so we can backdate created_at."""
    session.execute(
        text(
            """
            INSERT INTO transactions (
                hash, status, from_address, to_address, data, value, type,
                nonce, leader_only, execution_mode, appealed, appeal_failed,
                appeal_undetermined, appeal_leader_timeout,
                appeal_validators_timeout, appeal_processing_time,
                recovery_count, value_credited,
                consensus_history, consensus_data,
                created_at, blocked_at, worker_id,
                timestamp_awaiting_finalization
            ) VALUES (
                :hash, CAST(:status AS transaction_status),
                '0xfromaddress', :to_addr, CAST('{}' AS jsonb), 0, 2,
                :nonce, false, 'NORMAL', false, 0,
                false, false, false, 0, :recovery_count, false,
                CAST(:consensus_history AS jsonb), CAST(:consensus_data AS jsonb),
                :created_at, :blocked_at, :worker_id,
                :timestamp_awaiting_finalization
            )
            """
        ),
        {
            "hash": tx_hash,
            "status": status,
            "to_addr": to_address,
            "nonce": nonce,
            "created_at": created_at,
            "blocked_at": blocked_at,
            "worker_id": worker_id,
            "timestamp_awaiting_finalization": timestamp_awaiting_finalization,
            "recovery_count": recovery_count,
            "consensus_history": (
                json.dumps(consensus_history) if consensus_history is not None else None
            ),
            "consensus_data": (
                json.dumps(consensus_data) if consensus_data is not None else None
            ),
        },
    )


@pytest.mark.asyncio
async def test_long_queue_with_active_head_is_healthy(engine: Engine):
    """A contract with 50 PENDING and a head currently being processed
    must NOT show as orphaned. Long queues are fine if work is happening."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    contract = "0x" + "11" * 20

    with Session_() as s:
        # Head: COMMITTING with fresh blocked_at (active work).
        _insert_tx(
            s,
            tx_hash="0x" + "01" * 32,
            to_address=contract,
            status="COMMITTING",
            nonce=0,
            created_at=now - timedelta(hours=2),
            blocked_at=now - timedelta(seconds=30),
            worker_id="worker-alive",
        )
        # 50 queued PENDINGs behind it.
        for i in range(50):
            _insert_tx(
                s,
                tx_hash=f"0x{i+10:064x}",
                to_address=contract,
                status="PENDING",
                nonce=i + 1,
                created_at=now - timedelta(hours=1, minutes=i),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    health_module._rpc_router_ref = object()  # truthy stub for the guard
    result = await health_module._check_consensus_health()

    assert result["total_orphaned_transactions"] == 0, (
        "Long queue with an actively-progressing head must read 0 orphaned. "
        f"Got: {result}"
    )
    assert result["active_workers"] == 1, (
        "Worker holding the COMMITTING tx must be counted as active even "
        "though its tx is 2h old."
    )


@pytest.mark.asyncio
async def test_stuck_head_no_active_work_is_orphaned(engine: Engine):
    """Old head with NULL blocked_at and no other active work on the
    contract must show as a stuck head."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    contract = "0x" + "22" * 20

    with Session_() as s:
        # Head: 30 min old, no worker, no blocked_at.
        _insert_tx(
            s,
            tx_hash="0x" + "01" * 32,
            to_address=contract,
            status="COMMITTING",
            nonce=0,
            created_at=now - timedelta(minutes=30),
        )
        # 5 PENDINGs queued behind the stuck head.
        for i in range(5):
            _insert_tx(
                s,
                tx_hash=f"0x{i+10:064x}",
                to_address=contract,
                status="PENDING",
                nonce=i + 1,
                created_at=now - timedelta(minutes=20 - i),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    health_module._rpc_router_ref = object()
    result = await health_module._check_consensus_health()

    assert result["total_orphaned_transactions"] == 1, (
        "Stuck head should count its contract as orphaned regardless of "
        f"queue depth. Got: {result}"
    )
    # Single stuck head is below the degraded threshold (default 3) —
    # one sticky head can be transient and shouldn't page on its own.
    assert result["status"] == "healthy"


@pytest.mark.asyncio
async def test_recent_head_not_yet_orphaned(engine: Engine):
    """A head created within the stuck-after window is too new to flag
    even if there's no active worker on the contract — give it a chance."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    contract = "0x" + "33" * 20

    with Session_() as s:
        # Head 5 min old, no worker. Below the 15-min stuck threshold.
        _insert_tx(
            s,
            tx_hash="0x" + "01" * 32,
            to_address=contract,
            status="PENDING",
            nonce=0,
            created_at=now - timedelta(minutes=5),
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    health_module._rpc_router_ref = object()
    result = await health_module._check_consensus_health()

    assert result["total_orphaned_transactions"] == 0


@pytest.mark.asyncio
async def test_active_workers_counts_workers_processing_old_txs(engine: Engine):
    """Workers processing OLD txs (created hours ago) must still register
    as active. Previously they didn't because the query filtered by
    created_at > 1h ago, hiding workers from bursty-traffic deployments."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        _insert_tx(
            s,
            tx_hash="0x" + "01" * 32,
            to_address="0x" + "44" * 20,
            status="PROPOSING",
            nonce=0,
            created_at=now - timedelta(hours=6),  # very old
            blocked_at=now - timedelta(seconds=10),  # but worker is alive
            worker_id="worker-alive",
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    health_module._rpc_router_ref = object()
    result = await health_module._check_consensus_health()

    assert result["active_workers"] == 1, (
        "Worker processing a 6h-old tx with fresh blocked_at must count as "
        "active. Was the previous bug — alert fired falsely on bursty "
        "workloads."
    )
    assert result["total_orphaned_transactions"] == 0


@pytest.mark.asyncio
async def test_multiple_contracts_only_stuck_heads_count(engine: Engine):
    """With 3 contracts — one healthy (active head), one stuck head,
    one too-new — only the genuinely stuck one should count."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        # Contract A: head is being actively processed.
        _insert_tx(
            s,
            tx_hash="0x" + "0a" * 32,
            to_address="0x" + "aa" * 20,
            status="COMMITTING",
            nonce=0,
            created_at=now - timedelta(hours=1),
            blocked_at=now - timedelta(seconds=30),
            worker_id="worker-a",
        )
        # Contract B: head stuck (old, no work).
        _insert_tx(
            s,
            tx_hash="0x" + "0b" * 32,
            to_address="0x" + "bb" * 20,
            status="COMMITTING",
            nonce=0,
            created_at=now - timedelta(minutes=30),
        )
        # Contract C: head is fresh (within stuck-after window).
        _insert_tx(
            s,
            tx_hash="0x" + "0c" * 32,
            to_address="0x" + "cc" * 20,
            status="PENDING",
            nonce=0,
            created_at=now - timedelta(minutes=2),
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    health_module._rpc_router_ref = object()
    result = await health_module._check_consensus_health()

    assert result["total_orphaned_transactions"] == 1, (
        "Only Contract B's head is stuck. A is actively progressing, "
        f"C is too new. Got: {result}"
    )


@pytest.mark.asyncio
async def test_long_running_consensus_within_claim_window_not_stuck(engine: Engine):
    """A worker mid-execution holds blocked_at but doesn't refresh it.
    A 7-min-old blocked_at must STILL count as an active claim — the
    20/30-min worker timeout is the right window, not 5 min. Pre-fix
    this was the most likely false-positive source on LLM-heavy
    contracts where one consensus round can take >5 min."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    contract = "0x" + "5b" * 20

    with Session_() as s:
        # Head: 30 min old, currently being processed by a worker that
        # claimed it 7 min ago and hasn't released yet (long LLM call).
        _insert_tx(
            s,
            tx_hash="0x" + "01" * 32,
            to_address=contract,
            status="COMMITTING",
            nonce=0,
            created_at=now - timedelta(minutes=30),
            blocked_at=now - timedelta(minutes=7),
            worker_id="worker-busy",
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["total_orphaned_transactions"] == 0, (
        "A 7-min-old blocked_at is still within the claim window — "
        "worker is legitimately processing a long consensus round."
    )
    assert result["active_workers"] == 1


@pytest.mark.asyncio
async def test_detailed_consensus_endpoint_uses_claim_window_for_stuck_heads(
    engine: Engine,
):
    """The detailed /health/consensus endpoint must use the same claim-window
    semantics as /health. The old endpoint looked for workers on txs created
    in the last hour and falsely marked old, actively claimed txs orphaned."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    contract = "0x" + "5d" * 20

    with Session_() as s:
        _insert_tx(
            s,
            tx_hash="0x" + "01" * 32,
            to_address=contract,
            status="COMMITTING",
            nonce=0,
            created_at=now - timedelta(hours=2),
            blocked_at=now - timedelta(minutes=7),
            worker_id="worker-busy",
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module.health_consensus(rpc_router=object())

    assert result["active_workers"] == 1
    assert result["total_orphaned_transactions"] == 0
    assert result["contracts"][0]["orphaned_transactions"] == 0


@pytest.mark.asyncio
async def test_detailed_consensus_endpoint_excludes_post_consensus_statuses(
    engine: Engine,
):
    """Post-consensus rows are finalization backlog, not stuck consensus heads.
    The detailed endpoint should not reintroduce the old false-positive class."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i, st in enumerate(
            ["ACCEPTED", "UNDETERMINED", "LEADER_TIMEOUT", "VALIDATORS_TIMEOUT"]
        ):
            _insert_tx(
                s,
                tx_hash=f"0x5e{i:062x}",
                to_address=f"0x{(0x5E + i):02x}" + "00" * 19,
                status=st,
                nonce=0,
                created_at=now - timedelta(hours=2),
                blocked_at=now - timedelta(minutes=45),
                worker_id=f"old-worker-{i}",
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module.health_consensus(rpc_router=object())

    assert result["total_orphaned_transactions"] == 0
    assert all(
        contract["orphaned_transactions"] == 0 for contract in result["contracts"]
    )


@pytest.mark.asyncio
async def test_detailed_consensus_endpoint_reports_stuck_head_details(engine: Engine):
    """When heads are genuinely stuck, the detailed endpoint should expose the
    affected transaction hashes so alerts can be investigated from the payload."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i in range(3):
            _insert_tx(
                s,
                tx_hash=f"0x5f{i:062x}",
                to_address=f"0x{(0x5F + i):02x}" + "00" * 19,
                status="COMMITTING",
                nonce=0,
                created_at=now - timedelta(minutes=45 + i),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module.health_consensus(rpc_router=object())

    assert result["status"] == "degraded"
    assert result["total_orphaned_transactions"] == 3
    stuck_contracts = [
        contract
        for contract in result["contracts"]
        if contract["orphaned_transactions"]
    ]
    assert len(stuck_contracts) == 3
    assert all(
        contract["stuck_head_transaction"]["hash"]
        in contract["orphaned_transaction_hashes"]
        for contract in stuck_contracts
    )


@pytest.mark.asyncio
async def test_expired_claim_counts_contract_as_stuck(engine: Engine):
    """blocked_at older than the claim window means the worker has
    timed out. Recovery would reset it. Until then, the head is
    genuinely stuck — count its contract."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    contract = "0x" + "5c" * 20

    with Session_() as s:
        # blocked_at 45 min ago — well past the 30-min default claim
        # window. Worker is presumed dead.
        _insert_tx(
            s,
            tx_hash="0x" + "01" * 32,
            to_address=contract,
            status="COMMITTING",
            nonce=0,
            created_at=now - timedelta(minutes=60),
            blocked_at=now - timedelta(minutes=45),
            worker_id="worker-zombie",
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["total_orphaned_transactions"] == 1
    assert result["active_workers"] == 0, "Expired claim ⇒ worker not active."


@pytest.mark.asyncio
async def test_post_consensus_statuses_excluded_from_stuck_head_count(engine: Engine):
    """ACCEPTED / UNDETERMINED / *_TIMEOUT are post-consensus statuses,
    awaiting finalization rather than blocked on consensus progress.
    They MUST NOT count as stuck consensus heads — that's the false-
    positive class that mis-fired the production alert in May 2026
    (15 stranded UNDETERMINED rows on unused contracts looked like
    stuck heads, but they had zero queue behind them). The dedicated
    stuck-finalization detector covers these instead."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i, st in enumerate(
            ["ACCEPTED", "UNDETERMINED", "LEADER_TIMEOUT", "VALIDATORS_TIMEOUT"]
        ):
            _insert_tx(
                s,
                tx_hash=f"0x{i:064x}",
                to_address=f"0x{(0x60 + i):02x}" + "00" * 19,
                status=st,
                nonce=0,
                created_at=now - timedelta(minutes=30),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["total_orphaned_transactions"] == 0, (
        "Post-consensus statuses must not pollute the stuck-head signal. "
        f"Got: {result}"
    )


@pytest.mark.asyncio
async def test_status_threshold_matches_dashboard_alert_gate(engine: Engine):
    """consensus.status flips to "degraded" only at the same threshold
    the top-level alert fires (default 3). Below that, individual
    sticky heads stay "healthy" — they're often transient."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        # 3 stuck contracts → status must flip to degraded.
        for i in range(3):
            _insert_tx(
                s,
                tx_hash=f"0x{i:064x}",
                to_address=f"0x{(0x70 + i):02x}" + "00" * 19,
                status="COMMITTING",
                nonce=0,
                created_at=now - timedelta(minutes=30),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["total_orphaned_transactions"] == 3
    assert len(result["stuck_head_transactions"]) == 3
    assert result["stuck_head_transactions"][0]["status"] == "COMMITTING"
    assert (
        result["status"] == "degraded"
    ), f"3 stuck contracts must flip status to degraded. Got: {result}"


# ── Stuck-finalization detector ───────────────────────────────────
#
# Separate from the stuck-consensus-head detector. Post-consensus txs
# (ACCEPTED / UNDETERMINED / *_TIMEOUT) carry agreed consensus_data and
# only need finalization. They live under this metric. The detector
# has two arms:
#
#   1. timestamp_awaiting_finalization set + stale → finalizer not
#      making progress on these rows (e.g., finalization scheduler
#      starvation, finalization claim path broken).
#   2. timestamp_awaiting_finalization NULL + row is old → defensive:
#      catches future bugs like the May 2026 insufficient-balance SEND
#      path, which reached UNDETERMINED without ever stamping the
#      timestamp and was invisible to claim_next_finalization forever.


@pytest.mark.asyncio
async def test_stuck_finalization_with_stale_timestamp_is_flagged(engine: Engine):
    """timestamp_awaiting_finalization is set but older than the
    threshold — classic "finalizer not running" case."""
    import time

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    stale_ts = int(time.time()) - 24 * 3600  # 1 day ago
    created_at = now - timedelta(days=1)

    with Session_() as s:
        _insert_tx(
            s,
            tx_hash="0x" + "f1" * 32,
            to_address="0x" + "f1" * 20,
            status="ACCEPTED",
            nonce=0,
            created_at=created_at,
            timestamp_awaiting_finalization=stale_ts,
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert (
        result["stuck_finalization_count"] == 1
    ), f"Stale timestamp_awaiting_finalization must be flagged. Got: {result}"
    assert result["stuck_finalization_transactions"] == [
        {
            "hash": "0x" + "f1" * 32,
            "contract_address": "0x" + "f1" * 20,
            "status": "ACCEPTED",
            "created_at": pytest.approx(int(created_at.timestamp()), abs=1),
            "timestamp_awaiting_finalization": stale_ts,
            "blocked_at": None,
            "worker_id": None,
            "waiting_seconds": pytest.approx(24 * 3600, abs=5),
        }
    ]


@pytest.mark.asyncio
async def test_stuck_finalization_with_null_timestamp_is_flagged(engine: Engine):
    """timestamp_awaiting_finalization is NULL but the row is old —
    defensive coverage for the May 2026 insufficient-balance SEND bug
    pattern (post-consensus row without an awaiting timestamp, invisible
    to claim_next_finalization)."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(hours=2)

    with Session_() as s:
        _insert_tx(
            s,
            tx_hash="0x" + "f2" * 32,
            to_address="0x" + "f2" * 20,
            status="UNDETERMINED",
            nonce=0,
            created_at=created_at,
            timestamp_awaiting_finalization=None,
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["stuck_finalization_count"] == 1, (
        "NULL timestamp + old row must be flagged so a regression of the "
        f"insufficient-balance SEND bug is caught. Got: {result}"
    )
    assert result["stuck_finalization_transactions"] == [
        {
            "hash": "0x" + "f2" * 32,
            "contract_address": "0x" + "f2" * 20,
            "status": "UNDETERMINED",
            "created_at": pytest.approx(int(created_at.timestamp()), abs=1),
            "timestamp_awaiting_finalization": None,
            "blocked_at": None,
            "worker_id": None,
            "waiting_seconds": pytest.approx(2 * 3600, abs=5),
        }
    ]


@pytest.mark.asyncio
async def test_recent_finalization_eligible_row_is_not_flagged(engine: Engine):
    """Row younger than the stuck-finalization threshold must not be
    flagged — the finalization window is still open."""
    import time

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        _insert_tx(
            s,
            tx_hash="0x" + "f3" * 32,
            to_address="0x" + "f3" * 20,
            status="ACCEPTED",
            nonce=0,
            created_at=now - timedelta(seconds=30),
            # set to "now" — finality window not exceeded yet
            timestamp_awaiting_finalization=int(time.time()),
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert (
        result["stuck_finalization_count"] == 0
    ), f"Fresh finalization-eligible row must not be flagged. Got: {result}"
    assert result["stuck_finalization_transactions"] == []


@pytest.mark.asyncio
async def test_stuck_finalization_flips_status_at_threshold(engine: Engine):
    """consensus.status flips to degraded when stuck_finalization_count
    reaches HEALTH_DEGRADED_AT_STUCK_FINALIZATIONS (default 3) —
    independent of stuck-head count, so finalization stalls get their
    own clear signal."""
    import time

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    stale_ts = int(time.time()) - 12 * 3600

    with Session_() as s:
        for i in range(3):
            _insert_tx(
                s,
                tx_hash=f"0xf4{i:062x}",
                to_address=f"0x{(0xF4 + i):02x}" + "00" * 19,
                status="UNDETERMINED",
                nonce=0,
                created_at=now - timedelta(hours=12),
                timestamp_awaiting_finalization=stale_ts,
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["stuck_finalization_count"] == 3
    assert (
        result["status"] == "degraded"
    ), f"3 stuck finalizations must flip consensus.status. Got: {result}"


@pytest.mark.asyncio
async def test_recovery_storm_flags_poison_transaction(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    """A tx that has been recovered repeatedly is a high-confidence
    poison-tx signal even when blocked_at keeps looking fresh."""
    monkeypatch.setenv("HEALTH_RECOVERY_STORM_MIN_RECOVERIES", "2")

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        _insert_tx(
            s,
            tx_hash="0x" + "aa" * 32,
            to_address="0x" + "aa" * 20,
            status="PENDING",
            nonce=0,
            created_at=now - timedelta(minutes=1),
            recovery_count=2,
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["recovery_storm_count"] == 1
    assert result["max_recovery_count"] == 2
    assert result["status"] == "degraded"


@pytest.mark.asyncio
async def test_recent_max_recovery_exhaustion_flags_notice(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    """A transaction canceled after exhausting recovery cycles should surface
    as a degraded notice even though it no longer appears in active backlog."""
    monkeypatch.setenv("HEALTH_MAX_RECOVERY_EXHAUSTED_NOTICE_WINDOW_MINUTES", "60")

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        _insert_tx(
            s,
            tx_hash="0x" + "ab" * 32,
            to_address="0x" + "ab" * 20,
            status="CANCELED",
            nonce=0,
            created_at=now - timedelta(minutes=5),
            recovery_count=3,
            consensus_data={
                "error": "max_recovery_cycles_exceeded",
                "recovery_count": 3,
                "max_recovery_exhausted_at": int(now.timestamp()),
            },
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["max_recovery_exhausted_count"] == 1
    assert result["max_recovery_exhausted_transactions"] == [
        {
            "hash": "0x" + "ab" * 32,
            "contract_address": "0x" + "ab" * 20,
            "recovery_count": 3,
            "exhausted_at": int(now.timestamp()),
        }
    ]
    assert result["status"] == "degraded"


@pytest.mark.asyncio
async def test_old_backlog_without_recent_progress_is_flagged(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    """If an old backlog exists and no tx has reached ACCEPTED/FINALIZED
    recently, consensus is not producing outcomes."""
    monkeypatch.setenv("HEALTH_NO_PROGRESS_WINDOW_MINUTES", "10")
    monkeypatch.setenv("HEALTH_NO_PROGRESS_MIN_BACKLOG", "3")

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i in range(3):
            _insert_tx(
                s,
                tx_hash=f"0xb0{i:062x}",
                to_address="0x" + "b0" * 20,
                status="PENDING",
                nonce=i,
                created_at=now - timedelta(minutes=20 + i),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["no_consensus_progress"] is True
    assert result["no_progress_backlog_count"] == 3
    assert result["status"] == "degraded"


@pytest.mark.asyncio
async def test_no_progress_detector_skips_history_scan_without_backlog(engine: Engine):
    """No old backlog means consensus cannot be stalled, so the detector
    must not scan JSON consensus history. On Rally-scale datasets that scan
    can be expensive enough to wedge the background health task."""

    def fail_on_progress_scan(
        conn, cursor, statement, parameters, context, executemany
    ):
        if "current_monitoring" in statement:
            raise AssertionError("progress history scan should be skipped")

    event.listen(engine, "before_cursor_execute", fail_on_progress_scan)
    try:
        from backend.protocol_rpc import health as health_module

        result = await health_module._check_consensus_health()
    finally:
        event.remove(engine, "before_cursor_execute", fail_on_progress_scan)

    assert result["status"] == "healthy"
    assert result["no_consensus_progress"] is False
    assert result["no_progress_backlog_count"] == 0


@pytest.mark.asyncio
async def test_no_progress_scan_error_does_not_assert_outage(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    """If the optional JSON history scan times out, surface the check error
    but don't turn an unknown progress signal into a consensus outage."""
    monkeypatch.setenv("HEALTH_NO_PROGRESS_WINDOW_MINUTES", "10")
    monkeypatch.setenv("HEALTH_NO_PROGRESS_MIN_BACKLOG", "3")

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i in range(3):
            _insert_tx(
                s,
                tx_hash=f"0xd0{i:062x}",
                to_address="0x" + "d0" * 20,
                status="PENDING",
                nonce=i,
                created_at=now - timedelta(minutes=20 + i),
            )
        s.commit()

    def fail_on_progress_scan(
        conn, cursor, statement, parameters, context, executemany
    ):
        if "current_monitoring" in statement:
            raise RuntimeError("simulated progress scan timeout")

    event.listen(engine, "before_cursor_execute", fail_on_progress_scan)
    try:
        from backend.protocol_rpc import health as health_module

        result = await health_module._check_consensus_health()
    finally:
        event.remove(engine, "before_cursor_execute", fail_on_progress_scan)

    assert result["no_progress_check_error"] is True
    assert result["no_consensus_progress"] is False
    assert result["no_progress_backlog_count"] == 3
    assert result["status"] == "healthy"


@pytest.mark.asyncio
async def test_no_progress_scan_timeout_enters_cooldown(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    """After a timeout, skip the optional JSON scan briefly instead of
    re-running the same expensive query every health tick."""
    monkeypatch.setenv("HEALTH_NO_PROGRESS_WINDOW_MINUTES", "10")
    monkeypatch.setenv("HEALTH_NO_PROGRESS_MIN_BACKLOG", "3")
    monkeypatch.setenv("HEALTH_NO_PROGRESS_SCAN_ERROR_COOLDOWN_SECONDS", "300")

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i in range(3):
            _insert_tx(
                s,
                tx_hash=f"0xe0{i:062x}",
                to_address="0x" + "e0" * 20,
                status="PENDING",
                nonce=i,
                created_at=now - timedelta(minutes=20 + i),
            )
        s.commit()

    progress_scan_count = 0

    def fail_first_progress_scan(
        conn, cursor, statement, parameters, context, executemany
    ):
        nonlocal progress_scan_count
        if "current_monitoring" not in statement:
            return
        progress_scan_count += 1
        if progress_scan_count == 1:
            raise RuntimeError("simulated progress scan timeout")
        raise AssertionError("progress scan should be suppressed during cooldown")

    event.listen(engine, "before_cursor_execute", fail_first_progress_scan)
    try:
        from backend.protocol_rpc import health as health_module

        first = await health_module._check_consensus_health()
        second = await health_module._check_consensus_health()
    finally:
        event.remove(engine, "before_cursor_execute", fail_first_progress_scan)

    assert first["no_progress_check_error"] is True
    assert first["no_progress_scan_suppressed"] is False
    assert second["no_progress_check_error"] is True
    assert second["no_progress_scan_suppressed"] is True
    assert progress_scan_count == 1


@pytest.mark.asyncio
async def test_recent_consensus_progress_suppresses_no_progress_alert(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    """A fresh ACCEPTED/FINALIZED timestamp means the queue is draining,
    even if an older backlog still exists."""
    import time

    monkeypatch.setenv("HEALTH_NO_PROGRESS_WINDOW_MINUTES", "10")
    monkeypatch.setenv("HEALTH_NO_PROGRESS_MIN_BACKLOG", "3")

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i in range(3):
            _insert_tx(
                s,
                tx_hash=f"0xc0{i:062x}",
                to_address="0x" + "c0" * 20,
                status="PENDING",
                nonce=i,
                created_at=now - timedelta(minutes=20 + i),
            )
        _insert_tx(
            s,
            tx_hash="0x" + "c1" * 32,
            to_address="0x" + "c1" * 20,
            status="FINALIZED",
            nonce=99,
            created_at=now - timedelta(minutes=1),
            consensus_history={
                "current_monitoring": {
                    "ACCEPTED": time.time(),
                    "FINALIZED": time.time(),
                }
            },
        )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_consensus_health()

    assert result["no_consensus_progress"] is False
    assert result["no_progress_backlog_count"] == 3
