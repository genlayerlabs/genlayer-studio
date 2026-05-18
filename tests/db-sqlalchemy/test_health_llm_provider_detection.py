"""
Tests for /health's per-(provider, model) LLM failure-rate detection.

Background: a recent incident on Rally Prod was caused by IO Intelligence
returning HTTP 402 ("requires a higher IO Intelligence tier") for every
call from a specific validator entry. The dashboard alert read "There
are 58 pending transactions" — useless for diagnosis. The actual cause
was buried in worker stderr.

This test suite verifies the new `_check_llm_provider_health()` correctly
mines `transactions.consensus_data` (leader_receipt + validators) and
exposes:
  - `degraded` only when a (provider, model) crosses both the
    failure-rate AND sample-size thresholds
  - `no_data` when the window has zero receipts
  - `healthy` when failures are scattered or below thresholds
  - sanitized sample-error fields (no raw stderr / node_config)

Limitations the implementation acknowledges and we don't test for:
  - Fallback-rescued primary failures (Lua's try_provider returns
    SUCCESS as soon as any provider works, with no persisted record of
    the primary failed attempt). This metric only catches
    all-providers-down on a given (provider, model).
  - Window is on `created_at`, not "consensus ran in last N min" — the
    schema has no `status_changed_at`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker


@dataclass
class _StubManager:
    engine: Engine


@pytest.fixture(autouse=True)
def _wire_health_module_to_test_engine(engine: Engine, monkeypatch):
    """Point session_factory at the test engine; tighten thresholds so
    each test can drive the alert with a small handful of receipts."""
    from backend.database_handler import session_factory
    from backend.protocol_rpc import health as health_module

    prev_mgr = session_factory._db_manager
    prev_router = health_module._rpc_router_ref

    session_factory._db_manager = _StubManager(engine=engine)
    health_module._rpc_router_ref = object()

    # Lower MIN_SAMPLES so each test only needs a handful of rows.
    monkeypatch.setenv("LLM_PROVIDER_MIN_SAMPLES", "3")
    monkeypatch.setenv("LLM_PROVIDER_FAILURE_THRESHOLD", "0.5")
    monkeypatch.setenv("LLM_PROVIDER_WINDOW_MINUTES", "15")

    yield

    session_factory._db_manager = prev_mgr
    health_module._rpc_router_ref = prev_router


def _make_validator(
    *,
    provider: str,
    model: str,
    execution_result: str,
    error_code: str | None = None,
    causes: list | None = None,
    http_status: str | None = None,
    description: str | None = None,
) -> dict:
    return {
        "execution_result": execution_result,
        "node_config": {
            "address": "0xvalidator",
            # private_key intentionally present in real receipts; tests
            # confirm we don't surface it.
            "private_key": "0xdeadbeef" * 8,
            "stake": 100,
            "primary_model": {"provider": provider, "model": model},
            "secondary_model": None,
        },
        "genvm_result": {
            "stdout": "",
            "stderr": "MUST NEVER BE EXPOSED — contains LLM prompts and ctx",
            "error_code": error_code,
            "raw_error": (
                {
                    "causes": causes,
                    "ctx": {"status": http_status} if http_status else {},
                }
                if (causes or http_status)
                else None
            ),
            "error_description": description,
        },
    }


def _insert_tx_with_receipts(
    session,
    *,
    tx_hash: str,
    leader_receipt: list | None,
    validators: list,
    created_at: datetime,
    status: str = "FINALIZED",
):
    consensus_data = {}
    if leader_receipt is not None:
        consensus_data["leader_receipt"] = leader_receipt
    consensus_data["validators"] = validators
    session.execute(
        text(
            """
            INSERT INTO transactions (
                hash, status, from_address, to_address, data, value, type,
                nonce, leader_only, execution_mode, appealed, appeal_failed,
                appeal_undetermined, appeal_leader_timeout,
                appeal_validators_timeout, appeal_processing_time,
                recovery_count, value_credited,
                created_at, consensus_data
            ) VALUES (
                :hash, CAST(:status AS transaction_status),
                '0xfromaddr', '0xtoaddr', CAST('{}' AS jsonb), 0, 2,
                :nonce, false, 'NORMAL', false, 0,
                false, false, false, 0, 0, false,
                :created_at, CAST(:consensus_data AS jsonb)
            )
            """
        ),
        {
            "hash": tx_hash,
            "status": status,
            "nonce": int(tx_hash[2:18], 16) % 100000,
            "created_at": created_at,
            "consensus_data": __import__("json").dumps(consensus_data),
        },
    )


@pytest.mark.asyncio
async def test_no_data_when_no_recent_receipts(engine: Engine):
    """An empty window must report no_data, not degraded."""
    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    assert result["status"] == "no_data"
    assert result["alert_providers"] == []
    assert result["total_samples"] == 0


@pytest.mark.asyncio
async def test_one_provider_failing_at_100pct_alerts(engine: Engine):
    """The yesterday case: every call from one (provider, model) fails
    in the window. Must surface as a single alert_provider with the
    sample error fields."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i in range(5):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{i:064x}",
                leader_receipt=[
                    _make_validator(
                        provider="ionet",
                        model="Qwen/Qwen3-Next-80B-A3B-Instruct",
                        execution_result="ERROR",
                        causes=["STATUS_NOT_OK"],
                        http_status="402",
                        description=(
                            "Model 'Qwen/Qwen3-Next-80B-A3B-Instruct' "
                            "requires a higher IO Intelligence tier"
                        ),
                    )
                ],
                validators=[],
                created_at=now - timedelta(minutes=2 + i),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    assert result["status"] == "degraded"
    assert len(result["alert_providers"]) == 1
    a = result["alert_providers"][0]
    assert a["provider"] == "ionet"
    assert a["model"] == "Qwen/Qwen3-Next-80B-A3B-Instruct"
    assert a["samples"] == 5
    assert a["failures"] == 5
    assert a["failure_rate"] == 1.0
    # Sanitized sample error: structured fields only.
    err = a["sample_error"]
    assert err["http_status"] == "402"
    assert "tier" in err["description_brief"].lower()
    # Privacy: nothing leaks raw stderr or node_config.
    assert "stderr" not in err
    assert "private_key" not in str(result)


@pytest.mark.asyncio
async def test_below_threshold_does_not_alert(engine: Engine):
    """A provider failing at 30% should not cross the 0.5 alert threshold."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        # 10 receipts for openrouter/gpt-5.4: 3 failures, 7 successes (30%).
        for i in range(10):
            ok = i >= 3
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0xa0 + i):064x}",
                leader_receipt=None,
                validators=[
                    _make_validator(
                        provider="openrouter",
                        model="openai/gpt-5.4",
                        execution_result="SUCCESS" if ok else "ERROR",
                        causes=None if ok else ["STATUS_NOT_OK"],
                    )
                ],
                created_at=now - timedelta(minutes=1 + i),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    assert result["status"] == "healthy"
    assert result["alert_providers"] == []
    assert result["total_samples"] == 10


@pytest.mark.asyncio
async def test_below_min_samples_does_not_alert(engine: Engine):
    """Two failures out of two samples is 100% failure but below the
    min-samples floor — must not alert (could just be a flake)."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i in range(2):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0xb0 + i):064x}",
                leader_receipt=None,
                validators=[
                    _make_validator(
                        provider="openrouter",
                        model="z-ai/glm-5.1",
                        execution_result="ERROR",
                        causes=["STATUS_NOT_OK"],
                    )
                ],
                created_at=now - timedelta(minutes=1 + i),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    assert result["status"] == "healthy"
    assert result["alert_providers"] == []


@pytest.mark.asyncio
async def test_outside_window_excluded(engine: Engine):
    """Receipts older than the window must not contribute. Tests that
    the time filter works."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        # 5 ERROR receipts, but all created 2h ago — outside the
        # 15-minute window.
        for i in range(5):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0xc0 + i):064x}",
                leader_receipt=None,
                validators=[
                    _make_validator(
                        provider="ionet",
                        model="failing-model",
                        execution_result="ERROR",
                        causes=["STATUS_NOT_OK"],
                    )
                ],
                created_at=now - timedelta(hours=2),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    # No receipts in window → no_data.
    assert result["status"] == "no_data"
    assert result["total_samples"] == 0


@pytest.mark.asyncio
async def test_aggregates_across_leader_and_validators(engine: Engine):
    """The query unions both leader_receipt and validators arrays. A
    failing provider that only appears as a leader (not in validators)
    must still be detected."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        for i in range(4):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0xd0 + i):064x}",
                leader_receipt=[
                    _make_validator(
                        provider="ionet",
                        model="leader-only",
                        execution_result="ERROR",
                        causes=["STATUS_NOT_OK"],
                    )
                ],
                validators=[],
                created_at=now - timedelta(minutes=1 + i),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    assert result["status"] == "degraded"
    assert len(result["alert_providers"]) == 1
    assert result["alert_providers"][0]["provider"] == "ionet"
    assert result["alert_providers"][0]["model"] == "leader-only"


@pytest.mark.asyncio
async def test_only_terminal_status_txs_counted(engine: Engine):
    """In-flight rows (PROPOSING/COMMITTING/REVEALING) have mutable
    consensus_data and must be excluded — otherwise we double-count
    interim receipts that may flip back to non-error on the next round."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        # 5 ERROR receipts but on a tx still in PROPOSING.
        for i in range(5):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0xe0 + i):064x}",
                leader_receipt=None,
                validators=[
                    _make_validator(
                        provider="ionet",
                        model="in-flight-model",
                        execution_result="ERROR",
                        causes=["STATUS_NOT_OK"],
                    )
                ],
                created_at=now - timedelta(minutes=1 + i),
                status="PROPOSING",
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    # All rows are PROPOSING → excluded from the window.
    assert result["status"] == "no_data"
    assert result["total_samples"] == 0


@pytest.mark.asyncio
async def test_multiple_providers_only_failing_one_in_alert(engine: Engine):
    """One healthy provider, one failing — only the failing one shows
    in alert_providers."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        # openrouter/healthy: 5 SUCCESS
        for i in range(5):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0xf0 + i):064x}",
                leader_receipt=None,
                validators=[
                    _make_validator(
                        provider="openrouter",
                        model="healthy-model",
                        execution_result="SUCCESS",
                    )
                ],
                created_at=now - timedelta(minutes=1 + i),
            )
        # ionet/broken: 4 ERROR
        for i in range(4):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0x100 + i):064x}",
                leader_receipt=None,
                validators=[
                    _make_validator(
                        provider="ionet",
                        model="broken-model",
                        execution_result="ERROR",
                        causes=["STATUS_NOT_OK"],
                    )
                ],
                created_at=now - timedelta(minutes=1 + i),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    assert result["status"] == "degraded"
    assert len(result["alert_providers"]) == 1
    assert result["alert_providers"][0]["provider"] == "ionet"
    # total_samples spans both provider buckets.
    assert result["total_samples"] == 9


@pytest.mark.asyncio
async def test_contract_side_errors_do_not_count_as_llm_failures(engine: Engine):
    """Contract-side Python exceptions (e.g. user code calling a non-
    existent SDK attribute) surface as execution_result='ERROR' with
    `error_code` AND `raw_error` both NULL — the genvm crashed in user
    code before any LLM call.

    Without filtering, one broken contract running on a hot path makes
    every validator (across all models) look like an LLM provider
    failure. This is exactly what fired Studio Prod's nonstop
    llm_provider_failure alert in May 2026 (every openrouter model
    reported 50-75% error rate; turned out to be one user contract
    crashing with `AttributeError: module 'genlayer.gl' has no
    attribute 'ContractState'`).

    Pin: contract-side ERROR receipts (no error_code, no raw_error) MUST
    NOT count as LLM failures.
    """
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        # 30 receipts, all ERROR, all simulating a contract-side crash
        # (no structured error fields). At 30 samples this is well above
        # MIN_SAMPLES (25), so a naive counter would flip degraded.
        for i in range(30):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0x200 + i):064x}",
                leader_receipt=None,
                validators=[
                    _make_validator(
                        provider="openrouter",
                        model="openai/gpt-5.4",
                        execution_result="ERROR",
                        # error_code=None, causes=None, http_status=None
                        # → raw_error is None too. This is the contract-
                        # crash shape (stderr contains the Python
                        # traceback but we don't surface it).
                    )
                ],
                created_at=now - timedelta(minutes=1),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    # Sample count includes contract crashes (the receipt did happen),
    # but failure count for the alert excludes them. So:
    # - 30 samples total, 0 LLM failures → 0% failure rate → no alert.
    assert result["status"] == "healthy", (
        f"30 contract-crash ERROR receipts must NOT trigger an "
        f"llm_provider_failure alert. Got: {result}"
    )
    assert result["alert_providers"] == []


@pytest.mark.asyncio
async def test_contract_crashes_mixed_with_real_llm_failures(engine: Engine):
    """When the same (provider, model) sees both contract crashes and
    real LLM errors, only the LLM errors count toward the failure rate.

    Setup: 20 contract crashes + 10 real LLM errors → 30 samples, 10
    failures = 33% → below the 50% threshold → no alert. Without the
    filter this would be 30 samples, 30 failures = 100% → alert."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with Session_() as s:
        # 20 contract crashes
        for i in range(20):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0x300 + i):064x}",
                leader_receipt=None,
                validators=[
                    _make_validator(
                        provider="openrouter",
                        model="anthropic/claude-sonnet-4.6",
                        execution_result="ERROR",
                    )
                ],
                created_at=now - timedelta(minutes=2),
            )
        # 10 real LLM errors
        for i in range(10):
            _insert_tx_with_receipts(
                s,
                tx_hash=f"0x{(0x400 + i):064x}",
                leader_receipt=None,
                validators=[
                    _make_validator(
                        provider="openrouter",
                        model="anthropic/claude-sonnet-4.6",
                        execution_result="ERROR",
                        causes=["STATUS_NOT_OK"],
                        http_status="429",
                    )
                ],
                created_at=now - timedelta(minutes=2),
            )
        s.commit()

    from backend.protocol_rpc import health as health_module

    result = await health_module._check_llm_provider_health()

    # 30 samples, 10 LLM failures = 33% < 50% threshold → healthy
    assert (
        result["status"] == "healthy"
    ), f"Mixed crashes + LLM errors: only LLM errors should count. Got: {result}"
    assert result["alert_providers"] == []
