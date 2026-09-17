"""Guards against claim-query / transaction-dict drift.

The bug class: each worker claim query hand-maintained a RETURNING column
list plus a parallel row->dict conversion, and ``Transaction.from_dict``
silently defaults every missing key. A column omitted from one list was
therefore indistinguishable from a legitimately-absent value — which is how
claim_next_appeal shipped without contract_snapshot/consensus_history and a
successful validator appeal wiped contract state (PR #1724).

Three layers of defense, in order of strength:

1. RETURNING clauses and row->dict conversion are now *generated* from the
   shared column manifest in ``backend.consensus.worker`` — they cannot
   drift from each other. Tests here pin that each claim's executed SQL
   really contains its manifest.
2. The incident pin: the appeal claim manifest must include
   contract_snapshot and consensus_history.
3. The class-killer: every key ``Transaction.from_dict`` consumes must be
   either provided by a claim's manifest or listed in that claim's explicit
   omission allowlist below. Adding a new field to ``Transaction.from_dict``
   fails this test until someone makes a *conscious* per-claim decision.
"""

import inspect
import re

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.consensus.worker import (
    ConsensusWorker,
    _TX_APPEAL_COLUMNS,
    _TX_CLAIM_BASE_COLUMNS,
    _TX_FINALIZATION_COLUMNS,
    _TX_STATE_COLUMNS,
    _tx_returning_clause,
    _tx_row_to_dict,
)
from backend.domain.types import Transaction

# ---------------------------------------------------------------------------
# Per-claim manifests, mirroring the groups each claim function uses.
# ---------------------------------------------------------------------------
CLAIM_MANIFESTS = {
    "claim_next_transaction": (_TX_CLAIM_BASE_COLUMNS,),
    "claim_next_finalization": (_TX_CLAIM_BASE_COLUMNS, _TX_FINALIZATION_COLUMNS),
    "claim_next_appeal": (
        _TX_CLAIM_BASE_COLUMNS,
        _TX_STATE_COLUMNS,
        _TX_APPEAL_COLUMNS,
    ),
}

# ---------------------------------------------------------------------------
# Explicit omission allowlists (dict-key names as Transaction.from_dict
# consumes them). Every entry is a conscious decision: the from_dict default
# must be CORRECT for that claim's downstream, not merely tolerated.
# ---------------------------------------------------------------------------

# Fields recomputed during processing or purely informational, whose
# from_dict defaults are correct for every claim path:
_COMMON_OMISSIONS = {
    "appeal_processing_time",  # accounting value, only read for finality math from DB
    # config_rotation_rounds is NOT omitted: the message-emission path clamps
    # every triggered child to its parent's funded schedule, reading the value
    # straight off the claimed transaction rather than from the DB. The
    # from_dict default (None) is therefore wrong for all three claims.
    "num_of_initial_validators",  # config, re-read from DB where consumed
    "last_vote_timestamp",  # monitoring bookkeeping, rewritten during processing
    "rotation_count",  # reset on PENDING entry, tracked in-context afterwards
    "leader_timeout_validators",  # rebuilt by the timeout-appeal flow from DB
    "origin_address",  # informational; not consumed by worker flows
}

CLAIM_ALLOWED_OMISSIONS = {
    # Pending/activated claims: PendingState.handle refreshes the full
    # transaction from the DB before any field is consumed, so claim-dict
    # staleness cannot leak (see the refresh-first comment in PendingState).
    # contract_snapshot is deliberately NOT returned here: it can weigh tens
    # of MB and the pending path rebuilds snapshots via
    # contract_snapshot_factory instead.
    "claim_next_transaction": _COMMON_OMISSIONS
    | {
        "contract_snapshot",
        "consensus_history",
        "appealed",
        "appeal_failed",
        "appeal_undetermined",
        "appeal_leader_timeout",
        "appeal_validators_timeout",
        "timestamp_appeal",
        "timestamp_awaiting_finalization",
    },
    # Finalization: FinalizingState reads current contract state and merges
    # consensus_history via DB-side jsonb updates; the heavy state columns
    # are deliberately omitted.
    "claim_next_finalization": _COMMON_OMISSIONS
    | {
        "contract_snapshot",
        "consensus_history",
        "appealed",
        "appeal_undetermined",
        "appeal_leader_timeout",
        "appeal_validators_timeout",
        "timestamp_appeal",
    },
    # Appeals restore contract state from the claimed row — nothing
    # state-bearing may be omitted here.
    "claim_next_appeal": _COMMON_OMISSIONS | {"timestamp_awaiting_finalization"},
}


def _manifest_keys(groups) -> set:
    return {key for group in groups for _, key in group}


def _manifest_columns(groups) -> list:
    return [col for group in groups for col, _ in group]


def _from_dict_consumed_keys() -> set:
    """Statically extract every dict key Transaction.from_dict reads."""
    source = inspect.getsource(Transaction.from_dict)
    keys = set(re.findall(r"input\.get\(\s*[\"'](\w+)[\"']", source))
    keys |= set(re.findall(r"input\[[\"'](\w+)[\"']\]", source))
    assert keys, "failed to extract keys from Transaction.from_dict source"
    return keys


def _make_worker():
    worker = ConsensusWorker.__new__(ConsensusWorker)
    worker.worker_id = "worker-test"
    worker.transaction_timeout_minutes = 20
    worker._log_query_result = Mock()
    worker.consensus_algorithm = Mock(
        finality_window_time=1800, finality_window_appeal_failed_reduction=0.2
    )
    return worker


class TestManifestGeneratesQueries:
    """Each claim's executed SQL must contain exactly its manifest columns."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("claim_name", sorted(CLAIM_MANIFESTS))
    async def test_returning_clause_matches_manifest(self, claim_name):
        session = Mock()
        session.execute.return_value.first.return_value = None

        worker = _make_worker()
        result = await getattr(worker, claim_name)(session)
        assert result is None

        executed_sql = str(session.execute.call_args[0][0])
        returning_clause = executed_sql.split("RETURNING", 1)[1]
        for column in _manifest_columns(CLAIM_MANIFESTS[claim_name]):
            assert (
                f"transactions.{column}" in returning_clause
            ), f"{claim_name} RETURNING is missing manifest column {column!r}"

    def test_row_to_dict_covers_manifest(self):
        for claim_name, groups in CLAIM_MANIFESTS.items():
            columns = _manifest_columns(groups)
            row = SimpleNamespace(**{col: f"value-{col}" for col in columns})
            data = _tx_row_to_dict(row, *groups)
            assert set(data) == _manifest_keys(groups), claim_name
            # Every value must come from its column, aliases included.
            for group in groups:
                for col, key in group:
                    assert data[key] == f"value-{col}"

    def test_returning_clause_renders_all_groups(self):
        clause = _tx_returning_clause(
            _TX_CLAIM_BASE_COLUMNS, _TX_STATE_COLUMNS, _TX_APPEAL_COLUMNS
        )
        assert "transactions.contract_snapshot" in clause
        assert "transactions.consensus_history" in clause
        assert "transactions.triggered_by_hash" in clause


class TestIncidentPins:
    """Regression pins for the PR #1724 contract-state wipe."""

    def test_appeal_claim_includes_state_columns(self):
        keys = _manifest_keys(CLAIM_MANIFESTS["claim_next_appeal"])
        assert "contract_snapshot" in keys
        assert "consensus_history" in keys

    def test_every_claim_provides_config_rotation_rounds(self):
        # The message-emission path clamps each triggered child to its
        # parent's funded schedule, reading config_rotation_rounds off the
        # claimed transaction. When the claims omitted it, from_dict defaulted
        # it to None and every child insert raised TypeError, retrying until
        # the parent was cancelled.
        for claim_name, groups in CLAIM_MANIFESTS.items():
            assert "config_rotation_rounds" in _manifest_keys(groups), claim_name

    def test_triggered_by_alias_preserved(self):
        # Transaction.from_dict consumes "triggered_by", not the SQL column
        # name "triggered_by_hash".
        keys = _manifest_keys((_TX_CLAIM_BASE_COLUMNS,))
        assert "triggered_by" in keys
        assert "triggered_by_hash" not in keys


class TestFromDictCoverage:
    """The class-killer: silent from_dict defaults require explicit opt-out.

    If this test fails after you added a field to Transaction.from_dict,
    decide FOR EACH claim whether the query must return the new column
    (add it to a manifest group) or whether the from_dict default is truly
    correct for that claim's downstream (add it to the claim's allowlist
    with a rationale comment). Do not add blanket entries.
    """

    @pytest.mark.parametrize("claim_name", sorted(CLAIM_MANIFESTS))
    def test_consumed_keys_covered_or_explicitly_omitted(self, claim_name):
        consumed = _from_dict_consumed_keys()
        provided = _manifest_keys(CLAIM_MANIFESTS[claim_name])
        allowed = CLAIM_ALLOWED_OMISSIONS[claim_name]

        unaccounted = consumed - provided - allowed
        assert not unaccounted, (
            f"{claim_name}: Transaction.from_dict consumes {sorted(unaccounted)} "
            f"but the claim neither returns them nor allowlists the omission"
        )

        # Allowlists must not rot: entries that are actually provided, or that
        # from_dict no longer consumes, must be removed.
        stale = (allowed & provided) | (allowed - consumed)
        assert not stale, f"{claim_name}: stale allowlist entries {sorted(stale)}"
