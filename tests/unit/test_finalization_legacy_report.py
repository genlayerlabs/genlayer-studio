from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.database_handler.errors import ContractNotFoundError
from backend.database_handler.finalization_legacy_report import (
    UNRECOVERABLE,
    classify,
)
from backend.node.types import _compute_contract_state_hash


AFTER = {"slot": "after"}
OTHER = {"slot": "other"}


def row(expected=AFTER):
    return {
        "hash": "0xtx",
        "to_address": "0xcontract",
        "queue_order": 1,
        "contract_state_hash": _compute_contract_state_hash(expected),
    }


def processor(pre_states=None, successors=()):
    tp = Mock()
    tp.get_transaction_finalization_state.return_value = None
    tp.get_successor_snapshot_hashes.return_value = list(successors)
    tp.get_transaction_pre_state.side_effect = (pre_states or {}).get
    return tp


def head(state):
    return Mock(return_value=SimpleNamespace(states={"accepted": state}))


@pytest.mark.parametrize(
    "tp, live, expected",
    [
        (processor(), AFTER, ("head", "")),
        (processor({"0xtx": AFTER}), OTHER, ("own pre-state", "")),
        (
            processor({"0xs1": OTHER, "0xs2": AFTER}, ["0xs1", "0xs2"]),
            OTHER,
            ("successor 2 (0xs2)", ""),
        ),
    ],
)
def test_classifies_recoverable_sources(tp, live, expected):
    assert classify(row(), tp, head(live), Mock(return_value=False)) == expected


def test_missing_hash_is_unrecoverable_without_lookups():
    tp = processor()
    factory = head(AFTER)
    legacy = {**row(), "contract_state_hash": None}
    assert classify(legacy, tp, factory, Mock()) == (UNRECOVERABLE, "no hash")
    factory.assert_not_called()
    tp.get_transaction_pre_state.assert_not_called()


@pytest.mark.parametrize(
    "upgraded, reason", [(True, "upgrade after"), (False, "no matching snapshot")]
)
def test_unmatched_rows_report_why(upgraded, reason):
    tp = processor({"0xs1": OTHER}, ["0xs1"])
    upgraded_after = Mock(return_value=upgraded)
    assert classify(row(), tp, head(OTHER), upgraded_after) == (UNRECOVERABLE, reason)
    upgraded_after.assert_called_once_with(row())


def test_missing_contract_is_unrecoverable():
    factory = Mock(side_effect=ContractNotFoundError("0xcontract"))
    assert classify(row(), processor(), factory, Mock()) == (
        UNRECOVERABLE,
        "contract not found",
    )
