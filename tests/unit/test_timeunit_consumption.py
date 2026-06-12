import pytest

from backend.consensus.history import receipt_time_units, time_unit_consumption
from backend.consensus.types import ConsensusRound
from backend.database_handler.transactions_processor import TransactionsProcessor


def _receipt(mode, processing_time):
    return {"mode": mode, "processing_time": processing_time}


def _history(*entries):
    return {"consensus_results": list(entries)}


def _entry(consensus_round, leader_result=None, validator_results=None):
    return {
        "consensus_round": consensus_round,
        "leader_result": leader_result,
        "validator_results": validator_results,
    }


def _minimal_accounting():
    return {
        "paid_fee_value": 123,
        "user_value": 7,
        "fees_distribution": {
            "leaderTimeunitsAllocation": 100,
            "validatorTimeunitsAllocation": 200,
            "appealRounds": 0,
            "executionBudgetPerRound": 300,
            "executionConsumed": 0,
            "totalMessageFees": 400,
            "rotations": [0],
            "maxPriceGenPerTimeUnit": 5,
            "storageFeeMaxGasPrice": 6,
            "receiptFeeMaxGasPrice": 7,
        },
        "execution_fee_consumed": 11,
        "message_fee_consumed": 22,
        "message_fee_budget": 33,
        "execution_fee_report": {"genvmBuckets": {"storage": 44}},
    }


@pytest.mark.parametrize(
    "receipt,expected",
    [
        (None, 0),
        ({"processing_time": None}, 0),
        ({"processing_time": 0}, 0),
        ({"processing_time": -1}, 0),
        ({"processing_time": 1}, 1),
        ({"processing_time": 999}, 1),
        ({"processing_time": 1000}, 1),
        ({"processing_time": 1001}, 2),
        ({"processing_time": "1500"}, 2),
        ({"processing_time": "not-a-number"}, 0),
    ],
)
def test_receipt_time_units_rounds_up_defensively(receipt, expected):
    assert receipt_time_units(receipt) == expected


def test_time_unit_consumption_single_accepted_round():
    history = _history(
        _entry(
            ConsensusRound.ACCEPTED.value,
            leader_result=[_receipt("leader", 2300), _receipt("validator", 1100)],
            validator_results=[
                _receipt("validator", 900),
                _receipt("validator", 1000),
                _receipt("validator", 2500),
                _receipt("validator", 3001),
            ],
        )
    )

    assert time_unit_consumption(history, None) == {
        "leader_timeunits_used": 3,
        "validator_timeunits_used": 11,
        "per_round": [
            {
                "round": 0,
                "consensus_round": ConsensusRound.ACCEPTED.value,
                "leader_timeunits": 3,
                "validator_timeunits": 11,
                "max_validator_timeunits": 4,
            }
        ],
    }


def test_time_unit_consumption_attributes_rotation_to_next_round():
    history = _history(
        _entry(
            ConsensusRound.LEADER_ROTATION.value,
            leader_result=[_receipt("leader", 1500)],
        ),
        _entry(
            ConsensusRound.ACCEPTED.value,
            leader_result=[_receipt("leader", 2000)],
            validator_results=[_receipt("validator", 1000)],
        ),
    )

    consumption = time_unit_consumption(history, None)

    assert consumption["leader_timeunits_used"] == 4
    assert consumption["validator_timeunits_used"] == 1
    assert consumption["per_round"] == [
        {
            "round": 0,
            "consensus_round": ConsensusRound.ACCEPTED.value,
            "leader_timeunits": 4,
            "validator_timeunits": 1,
            "max_validator_timeunits": 1,
        }
    ]


def test_time_unit_consumption_multi_round_totals():
    history = _history(
        _entry(
            ConsensusRound.ACCEPTED.value,
            leader_result=[_receipt("leader", 1000)],
            validator_results=[_receipt("validator", 1500)],
        ),
        _entry(
            ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
            leader_result=[_receipt("leader", 2001)],
            validator_results=[_receipt("validator", 0), _receipt("validator", 2500)],
        ),
    )

    consumption = time_unit_consumption(history, None)

    assert consumption["leader_timeunits_used"] == 4
    assert consumption["validator_timeunits_used"] == 5
    assert consumption["per_round"] == [
        {
            "round": 0,
            "consensus_round": ConsensusRound.ACCEPTED.value,
            "leader_timeunits": 1,
            "validator_timeunits": 2,
            "max_validator_timeunits": 2,
        },
        {
            "round": 1,
            "consensus_round": ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
            "leader_timeunits": 3,
            "validator_timeunits": 3,
            "max_validator_timeunits": 3,
        },
    ]


def test_time_unit_consumption_trailing_rotation_emits_final_entry():
    history = _history(
        _entry(
            ConsensusRound.LEADER_ROTATION_APPEAL.value,
            leader_result=[_receipt("leader", 1500)],
            validator_results=[_receipt("validator", 1)],
        )
    )

    assert time_unit_consumption(history, None) == {
        "leader_timeunits_used": 2,
        "validator_timeunits_used": 1,
        "per_round": [
            {
                "round": 0,
                "consensus_round": ConsensusRound.LEADER_ROTATION_APPEAL.value,
                "leader_timeunits": 2,
                "validator_timeunits": 1,
                "max_validator_timeunits": 1,
            }
        ],
    }


def test_time_unit_consumption_falls_back_to_consensus_data_only_without_history():
    consensus_data = {
        "leader_receipt": [_receipt("leader", 1001), _receipt("validator", 1000)],
        "validators": [
            _receipt("validator", 2001),
            {"receipt": _receipt("validator", 0)},
        ],
    }

    consumption = time_unit_consumption(None, consensus_data)

    assert consumption == {
        "leader_timeunits_used": 2,
        "validator_timeunits_used": 4,
        "per_round": [
            {
                "round": 0,
                "consensus_round": "",
                "leader_timeunits": 2,
                "validator_timeunits": 4,
                "max_validator_timeunits": 3,
            }
        ],
    }


def test_time_unit_consumption_history_present_ignores_consensus_data():
    history = _history(
        _entry(
            ConsensusRound.ACCEPTED.value,
            leader_result=[_receipt("leader", 1)],
        )
    )
    consensus_data = {
        "leader_receipt": [_receipt("leader", 9000)],
        "validators": [_receipt("validator", 9000)],
    }

    assert time_unit_consumption(history, consensus_data) == {
        "leader_timeunits_used": 1,
        "validator_timeunits_used": 0,
        "per_round": [
            {
                "round": 0,
                "consensus_round": ConsensusRound.ACCEPTED.value,
                "leader_timeunits": 1,
                "validator_timeunits": 0,
                "max_validator_timeunits": 0,
            }
        ],
    }


@pytest.mark.parametrize(
    "consensus_history",
    [
        "bad",
        {"consensus_results": "bad"},
        {
            "consensus_results": [
                None,
                {"consensus_round": ConsensusRound.ACCEPTED.value},
            ]
        },
        {
            "consensus_results": [
                _entry(
                    ConsensusRound.ACCEPTED.value,
                    leader_result=[{"processing_time": 1000}],
                    validator_results=[_receipt("unknown", 1000), None],
                )
            ]
        },
    ],
)
def test_time_unit_consumption_malformed_input_does_not_crash(consensus_history):
    consumption = time_unit_consumption(consensus_history, None)

    assert consumption["leader_timeunits_used"] == 0
    assert consumption["validator_timeunits_used"] == 0


def test_canonical_fees_includes_time_unit_consumption():
    history = _history(
        _entry(
            ConsensusRound.ACCEPTED.value,
            leader_result=[_receipt("leader", 2300), _receipt("validator", 1100)],
            validator_results=[_receipt("validator", 900)],
        )
    )

    fees = TransactionsProcessor._canonical_fees(
        _minimal_accounting(),
        consensus_history=history,
        consensus_data={"leader_receipt": [_receipt("leader", 9999)]},
    )

    assert fees["consumed"]["executionConsumed"] == "11"
    assert fees["consumed"]["storageFeeUsed"] == "44"
    assert fees["consumed"]["messageFeesConsumed"] == "22"
    assert fees["consumed"]["messageFeesBudgetTotal"] == "33"
    assert fees["consumed"]["leaderTimeunitsUsed"] == "3"
    assert fees["consumed"]["validatorTimeunitsUsed"] == "3"
    assert fees["consumed"]["perRound"] == [
        {
            "round": 0,
            "consensusRound": ConsensusRound.ACCEPTED.value,
            "leaderTimeunits": "3",
            "validatorTimeunits": "3",
            "maxValidatorTimeunits": "2",
        }
    ]
    assert TransactionsProcessor._canonical_fees(None) is None


def test_canonical_fees_no_history_or_consensus_data_reports_zero_time_units():
    fees = TransactionsProcessor._canonical_fees(_minimal_accounting())

    assert fees["consumed"]["leaderTimeunitsUsed"] == "0"
    assert fees["consumed"]["validatorTimeunitsUsed"] == "0"
    assert fees["consumed"]["perRound"] == []
