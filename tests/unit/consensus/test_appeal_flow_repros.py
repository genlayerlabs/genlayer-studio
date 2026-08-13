"""Failing repros for consensus appeal-flow bugs (no fixes applied).

Three verified bugs reachable through the JSON-RPC appeal path
(_handle_appeal_or_top_up_and_submit -> process_validator_appeal), all of which
crash-loop the worker's generic-error retry so the transaction can never
re-execute:

  1. After a successful validator appeal the tx returns to PENDING with
     `appealed` still True; PendingState's appealed branch has no
     new-validator fallback (unlike the non-appealed branch), so if the
     original validators are gone it produces an empty validator set and
     ProposingState crashes unpacking it.
  2. The validator-appeal timeout-replacement pool includes the appealed
     leader, so a timed-out juror can be replaced by the leader whose receipt
     is under appeal -- the leader then votes on its own receipt.
  3. Appealing a LEADER_ONLY-accepted tx (leader_receipt length 1) makes
     CommittingState run context.leader == {} as a validator ->
     KeyError('address').

Each test asserts the CORRECT behavior and FAILS on current code.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.consensus.base import CommittingState, PendingState, ProposingState
from backend.database_handler.types import ConsensusData
from backend.domain.types import Transaction, TransactionType
from backend.node.genvm.origin.public_abi import ResultCode
from backend.node.types import ExecutionMode, ExecutionResultStatus, Receipt, Vote


class _MessageHandler:
    def send_message(self, *_args, **_kwargs):
        return None


def _make_receipt(
    address: str, vote: Vote | None, mode=ExecutionMode.VALIDATOR
) -> Receipt:
    return Receipt(
        result=bytes([ResultCode.RETURN]) + b"ok",
        calldata=b"",
        gas_used=0,
        mode=mode,
        contract_state={},
        node_config={"address": address},
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=vote,
        genvm_result={"raw_error": {"fatal": False}},
    )


def _snapshot_node(address: str) -> SimpleNamespace:
    validator = MagicMock()
    validator.to_dict.return_value = {"address": address, "stake": 1}
    return SimpleNamespace(validator=validator)


# ────────────────────────────────────────────────────────────────────
# BUG 1: PendingState appealed-branch has no fallback when the original
# validators are gone -> involved_validators == [] -> ProposingState
# crashes unpacking the empty list.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_appealed_branch_falls_back_when_original_validators_missing():
    """After a successful validator appeal the tx returns to PENDING with
    appealed=True still set in the DB. If the validators referenced in
    consensus_data are no longer registered, the appealed branch must fall
    back to selecting new validators (like the non-appealed branch at
    base.py:2021 does) instead of returning an empty involved_validators."""

    old_leader = _make_receipt("old-leader", None, mode=ExecutionMode.LEADER)
    old_v1 = _make_receipt("old-v1", Vote.AGREE)
    old_v2 = _make_receipt("old-v2", Vote.DISAGREE)

    consensus_data_dict = ConsensusData(
        votes={"old-v1": "agree", "old-v2": "disagree"},
        leader_receipt=[old_leader],
        validators=[old_v1, old_v2],
    ).to_dict()

    tx_dict = {
        "hash": "tx-appealed",
        "status": "PENDING",
        "type": TransactionType.RUN_CONTRACT.value,
        "from_address": "from",
        "to_address": "to",
        "value": 0,
        "appealed": True,  # left True by a successful validator appeal
        "consensus_data": consensus_data_dict,
        "consensus_history": {},
        "num_of_initial_validators": 2,
        "config_rotation_rounds": 3,
    }

    tp = MagicMock()
    tp.get_transaction_by_hash.return_value = tx_dict
    tp.set_transaction_appeal_validators_timeout.return_value = False

    # Current validator set: entirely new addresses.
    snapshot = SimpleNamespace(nodes=[_snapshot_node("new-1"), _snapshot_node("new-2")])

    context = SimpleNamespace(
        transaction=Transaction.from_dict(tx_dict),
        transactions_processor=tp,
        msg_handler=_MessageHandler(),
        consensus_service=MagicMock(),
        contract_processor=MagicMock(),
        accounts_manager=MagicMock(),
        contract_snapshot=None,
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=snapshot,
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        involved_validators=[],
        remaining_validators=[],
        leader={},
        votes={},
        validation_results=[],
        consensus_data=ConsensusData(votes={}, leader_receipt=None, validators=[]),
    )

    next_state = await PendingState().handle(context)
    assert isinstance(next_state, ProposingState)

    # EXPECTED (like the non-appealed branch): fall back to new validators.
    # ACTUAL: empty list -> ProposingState raises ValueError on unpack.
    assert context.involved_validators, (
        "appealed re-execution must not produce an empty validator set; "
        "ProposingState will crash unpacking it"
    )


# ────────────────────────────────────────────────────────────────────
# BUG 2: In a validator appeal, the timeout-replacement pool contains
# the ORIGINAL (appealed) leader, which can then vote on its own receipt.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_appeal_replacement_pool_excludes_appealed_leader(monkeypatch):
    """process_validator_appeal enters CommittingState with context.leader
    unset ({}) and remaining_validators = appeal validators. The replacement
    pool built at base.py:2318-2327 is 'all snapshot validators minus
    assigned', which INCLUDES the original leader whose receipt is being
    appealed. get_extra_validators explicitly excludes used leaders
    (base.py:1305-1313); the replacement path bypasses that."""

    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "0.05")

    async def exec_hung(_tx):
        await asyncio.sleep(0.5)
        return _make_receipt("appeal-1", Vote.AGREE)

    def node_factory(validator, *_args):
        if validator["address"] == "appeal-1":
            return SimpleNamespace(exec_transaction=exec_hung)

        async def exec_fast(_tx, address=validator["address"]):
            return _make_receipt(address, Vote.AGREE)

        return SimpleNamespace(exec_transaction=exec_fast)

    tp = MagicMock()

    # Appeal context, exactly as process_validator_appeal builds it:
    # leader is NOT set, leader_receipt has length 2 (post-split).
    context = SimpleNamespace(
        transaction=SimpleNamespace(hash="tx-appeal"),
        transactions_processor=tp,
        msg_handler=_MessageHandler(),
        consensus_service=MagicMock(),
        contract_processor=MagicMock(),
        node_factory=node_factory,
        contract_snapshot=MagicMock(),
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=SimpleNamespace(
            nodes=[
                _snapshot_node("old-leader"),  # the appealed leader
                _snapshot_node("appeal-1"),
                _snapshot_node("appeal-2"),
            ]
        ),
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        leader={},  # never set in process_validator_appeal
        remaining_validators=[{"address": "appeal-1"}, {"address": "appeal-2"}],
        consensus_data=ConsensusData(
            votes={},
            leader_receipt=[
                _make_receipt("old-leader", None, mode=ExecutionMode.LEADER),
                _make_receipt("old-leader", Vote.AGREE),
            ],
            validators=[],
        ),
        validation_results=[],
    )

    await CommittingState().handle(context)

    voter_addresses = [r.node_config["address"] for r in context.validation_results]
    # EXPECTED: the appealed leader must never vote in its own appeal.
    assert (
        "old-leader" not in voter_addresses
    ), f"appealed leader was drafted as replacement juror: {voter_addresses}"


# ────────────────────────────────────────────────────────────────────
# BUG 3: Validator appeal of a LEADER_ONLY-accepted tx (leader_receipt
# length 1) makes CommittingState run context.leader == {} as a validator
# -> KeyError('address') in node_factory -> appeal crashes forever.
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_appeal_of_leader_only_tx_does_not_run_empty_leader(monkeypatch):
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "1")

    def node_factory(validator, *_args):
        # Mirrors production node_factory (base.py:300-301): first thing it
        # does is validator["address"] -> KeyError on {}.
        address = validator["address"]

        async def exec_fast(_tx):
            return _make_receipt(address, Vote.AGREE)

        return SimpleNamespace(exec_transaction=exec_fast)

    context = SimpleNamespace(
        transaction=SimpleNamespace(hash="tx-leader-only-appeal"),
        transactions_processor=MagicMock(),
        msg_handler=_MessageHandler(),
        consensus_service=MagicMock(),
        contract_processor=MagicMock(),
        node_factory=node_factory,
        contract_snapshot=MagicMock(),
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=SimpleNamespace(
            nodes=[
                _snapshot_node("old-leader"),
                _snapshot_node("appeal-1"),
                _snapshot_node("appeal-2"),
                _snapshot_node("appeal-3"),
            ]
        ),
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        leader={},  # never set in process_validator_appeal
        remaining_validators=[
            {"address": "appeal-1"},
            {"address": "appeal-2"},
            {"address": "appeal-3"},
        ],
        # LEADER_ONLY acceptance stores leader_receipt of length 1
        consensus_data=ConsensusData(
            votes={},
            leader_receipt=[
                _make_receipt("old-leader", Vote.AGREE, mode=ExecutionMode.LEADER)
            ],
            validators=[],
        ),
        validation_results=[],
    )

    # EXPECTED: the appeal runs only the appeal validators and proceeds.
    next_state = await CommittingState().handle(context)
    assert next_state.__class__.__name__ == "RevealingState"
    voter_addresses = [r.node_config["address"] for r in context.validation_results]
    assert {} not in [r.node_config for r in context.validation_results]
    assert voter_addresses == ["appeal-1", "appeal-2", "appeal-3"]
