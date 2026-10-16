"""Finalization state provenance and backward-compatible snapshot decoding."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.consensus.base import _transaction_finalization_state
from backend.consensus.errors import FinalizationStateError
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.node.types import _compute_contract_state_hash


BEFORE = {"slot": "before"}
AFTER = {"slot": "after"}
YOUNGER = {"slot": "younger"}


def context(*, saved=None, stored=None, live=YOUNGER):
    tp = Mock()
    tp.get_transaction_finalization_state.return_value = stored
    factory = Mock(return_value=SimpleNamespace(states={"accepted": live}))
    return SimpleNamespace(
        transaction=SimpleNamespace(
            hash="0xtx", to_address="0xcontract", contract_snapshot=saved
        ),
        transactions_processor=tp,
        contract_snapshot_factory=factory,
    )


def receipt(*, state=None, expected=AFTER):
    return SimpleNamespace(
        contract_state=state, contract_state_hash=_compute_contract_state_hash(expected)
    )


def test_snapshot_roundtrip_keeps_pre_and_post_state_separate():
    saved = ContractSnapshot.from_dict(
        {
            "contract_address": "0xcontract",
            "states": {"accepted": BEFORE, "finalized": {}},
            "finalization_state": AFTER,
        }
    )
    restored = ContractSnapshot.from_dict(saved.to_dict())
    assert restored.states["accepted"] == BEFORE
    assert restored.finalization_state == AFTER


def test_old_snapshot_has_no_finalization_state():
    assert (
        ContractSnapshot.from_dict({"states": {"accepted": BEFORE}}).finalization_state
        is None
    )


def test_uses_transaction_snapshot_without_reading_live_contract():
    ctx = context(saved=SimpleNamespace(finalization_state=AFTER))
    assert _transaction_finalization_state(ctx, receipt()) == AFTER
    ctx.transactions_processor.get_transaction_finalization_state.assert_not_called()
    ctx.contract_snapshot_factory.assert_not_called()


def test_narrow_worker_claim_reloads_only_post_state():
    ctx = context(stored=AFTER)
    assert _transaction_finalization_state(ctx, receipt()) == AFTER
    ctx.transactions_processor.get_transaction_finalization_state.assert_called_once_with(
        "0xtx"
    )
    ctx.contract_snapshot_factory.assert_not_called()


def test_saved_empty_state_is_valid_without_hash():
    ctx = context(stored={})
    assert _transaction_finalization_state(ctx, receipt(expected={})) == {}
    ctx.contract_snapshot_factory.assert_not_called()


@pytest.mark.parametrize("source", ["snapshot", "database", "receipt"])
def test_mismatched_post_state_is_rejected(source):
    ctx = context(
        saved=(
            SimpleNamespace(finalization_state=YOUNGER)
            if source == "snapshot"
            else None
        ),
        stored=YOUNGER if source == "database" else None,
    )
    with pytest.raises(FinalizationStateError, match="hash mismatch"):
        _transaction_finalization_state(
            ctx, receipt(state=YOUNGER if source == "receipt" else None)
        )
    ctx.contract_snapshot_factory.assert_not_called()


def test_legacy_unstripped_receipt_remains_usable():
    ctx = context()
    assert _transaction_finalization_state(ctx, receipt(state=AFTER)) == AFTER
    ctx.contract_snapshot_factory.assert_not_called()


def test_legacy_live_head_requires_exact_hash_match():
    assert _transaction_finalization_state(context(live=AFTER), receipt()) == AFTER
    with pytest.raises(FinalizationStateError, match="unverified contract head"):
        _transaction_finalization_state(context(), receipt())


def test_legacy_stripped_receipt_without_hash_cannot_authorize_live_state():
    ctx = context(live=AFTER)
    with pytest.raises(FinalizationStateError, match="Missing transaction-local"):
        _transaction_finalization_state(ctx, receipt(state={}, expected=None))
    ctx.contract_snapshot_factory.assert_not_called()
