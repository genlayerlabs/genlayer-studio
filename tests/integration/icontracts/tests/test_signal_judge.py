# tests/integration/icontracts/tests/test_signal_judge.py
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
import json


def test_signal_judge_submit(setup_validators):
    # Phase 1: submit_signal makes no LLM call — no mock needed
    setup_validators({})

    factory = get_contract_factory("SignalJudge")
    contract = factory.deploy(args=[])

    tx = contract.submit_signal(
        args=[
            "BTC",
            "BTC will hit $100k",
            "ETF inflows accelerating",
            "100000",
            "ABOVE",
            "test",  # zero-second deadline, immediately resolvable
        ]
    ).transact()

    assert tx_execution_succeeded(tx)

    count = contract.get_signal_count(args=[]).call()
    assert count == 1

    # signal should be PENDING, leaderboard not updated yet
    pending = json.loads(contract.get_signals_by_status(args=["PENDING"]).call())
    assert len(pending) == 1
    assert pending[0]["asset"] == "BTC"
    assert pending[0]["status"] == "PENDING"

    sender = contract.account.address
    score = contract.get_score(args=[sender]).call()
    assert score["total"] == 0  # not resolved yet


def test_signal_judge_resolve(setup_validators):
    asset = "BTC"

    mock_response = {
        "response": {
            f"Asset: {asset}": json.dumps(
                {
                    "correct": False,
                    "current_price": "81000.00000000",
                    "reasoning_quality": 7,
                }
            ),
        },
        "eq_principle_prompt_comparative": {
            "The boolean field 'correct' must have the same value across all answers. "
            "Ignore any differences in 'current_price' (which varies by cents because it "
            "is fetched at different timestamps) and ignore differences in "
            "'reasoning_quality' (which is a subjective rating).": True
        },
    }
    setup_validators(mock_response)

    factory = get_contract_factory("SignalJudge")
    contract = factory.deploy(args=[])

    # submit first
    contract.submit_signal(
        args=["BTC", "BTC will hit $100k", "ETF inflows", "100000", "ABOVE", "test"]
    ).transact()

    # resolve immediately (deadline=0 so now >= deadline_ts)
    tx = contract.resolve_signal(args=[0]).transact()
    assert tx_execution_succeeded(tx)

    # signal should be RESOLVED
    resolved = json.loads(contract.get_signals_by_status(args=["RESOLVED"]).call())
    assert len(resolved) == 1
    assert resolved[0]["status"] == "RESOLVED"
    assert resolved[0]["correct"] == False

    # leaderboard updated
    sender = contract.account.address
    score = contract.get_score(args=[sender]).call()
    assert score["total"] == 1
    assert score["wins"] == 0
    assert score["win_rate_pct"] == "0"
