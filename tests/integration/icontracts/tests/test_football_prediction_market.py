# tests/e2e/test_storage.py
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
import json


def test_football_prediction_market(setup_validators):
    team_1 = "Georgia"
    team_2 = "Portugal"
    game_date = "2024-06-26"
    resolution_url = f"https://www.bbc.com/sport/football/scores-fixtures/{game_date}"
    score = "2:0"
    winner = 1
    mock_response = {
        "response": {
            f"Team 1: {team_1}\nTeam 2: {team_2}": json.dumps(
                {
                    "score": score,
                    "winner": winner,
                }
            ),
        }
    }
    mock_web_response = {
        "nondet_web_render": {
            resolution_url: {
                "mode": "text",
                "status": 200,
                "body": f"Full time: {team_1} {score} {team_2}",
            },
        },
    }
    setup_validators(mock_response, mock_web_response)

    # Deploy Contract
    factory = get_contract_factory("PredictionMarket")
    contract = factory.deploy(args=[game_date, team_1, team_2])

    ########################################
    ############# RESOLVE match ############
    ########################################
    transaction_response_call_1 = contract.resolve(args=[]).transact()
    assert tx_execution_succeeded(transaction_response_call_1)

    # Get Updated State
    contract_state_2 = contract.get_resolution_data(args=[]).call()

    assert contract_state_2["winner"] == winner
    assert contract_state_2["score"] == score
    assert contract_state_2["has_resolved"] == True
