# tests/integration/icontracts/tests/test_signal_judge.py
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
import json


def test_signal_judge(setup_validators):
    asset = "BTC"
    target_price = "100000"
    direction = "ABOVE"

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
            "The boolean field 'correct' must have the same value across all answers. Ignore any differences in 'current_price' (which varies by cents because it is fetched at different timestamps) and ignore differences in 'reasoning_quality' (which is a subjective rating).": True
        },
    }
    setup_validators(mock_response)

    factory = get_contract_factory("SignalJudge")
    contract = factory.deploy(args=[])

    transaction_response = contract.submit_signal(
        args=[
            asset,
            "BTC will hit $100k this week",
            "ETF inflows are accelerating",
            target_price,
            direction,
        ]
    ).transact()

    assert tx_execution_succeeded(transaction_response)

    # verify signal was recorded
    count = contract.get_signal_count(args=[]).call()
    assert count == 1

    # verify leaderboard updated (correct=False so wins=0, total=1)
    sender = contract.account.address
    score = contract.get_score(args=[sender]).call()
    assert score["total"] == 1
    assert score["wins"] == 0
    assert score["win_rate_pct"] == "0"