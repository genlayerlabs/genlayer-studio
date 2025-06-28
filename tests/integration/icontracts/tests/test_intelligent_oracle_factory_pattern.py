import time

from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
import json


def wait_for_contract_deployment(intelligent_oracle_contract, max_retries=10, delay=5):
    """
    Wait for intelligent oracle contract to be fully deployed by attempting to call a method.
    This is used to check if the triggered deployment did deploy the contract.
    """
    for _ in range(max_retries):
        try:
            intelligent_oracle_contract.get_dict(args=[])
            return True  # If successful, contract is deployed
        except Exception:
            time.sleep(delay)
    return False


def test_intelligent_oracle_factory_pattern(setup_validators):
    markets_data = [
        {
            "prediction_market_id": "marathon2024",
            "title": "Marathon Winner Prediction",
            "description": "Predict the male winner of a major marathon event.",
            "potential_outcomes": ["Bekele Fikre", "Tafa Mitku", "Chebii Douglas"],
            "rules": [
                "The outcome is based on the official race results announced by the marathon organizers."
            ],
            "data_source_domains": ["thepostrace.com"],
            "resolution_urls": [],
            "earliest_resolution_date": "2024-01-01T00:00:00+00:00",
            "outcome": "Tafa Mitku",
            "evidence_urls": "https://thepostrace.com/en/blog/marathon-de-madrid-2024-results-and-rankings/?srsltid=AfmBOor1uG6O3_4oJ447hkah_ilOYuy0XXMvl8j70EApe1Z7Bzd94XJl",
        },
        {
            "prediction_market_id": "election2024",
            "title": "Election Prediction",
            "description": "Predict the winner of the 2024 US presidential election.",
            "potential_outcomes": ["Kamala Harris", "Donald Trump"],
            "rules": ["The outcome is based on official election results."],
            "data_source_domains": ["bbc.com"],
            "resolution_urls": [],
            "earliest_resolution_date": "2024-01-01T00:00:00+00:00",
            "outcome": "Donald Trump",
            "evidence_urls": "https://www.bbc.com/news/election/2024/us/results",
        },
    ]

    reasoning_single_source_marathon = "The HTML content contains the results of the Madrid Marathon 2024, which occurred on April 28, 2024. Mitku Tafa won and matches the name 'Tafa Mitku' in the list of potential outcomes."
    reasoning_all_sources_marathon = "The URL indicates that the Madrid Marathon 2024 has occurred on April 28, 2024, and Mitku Tafa was the winner. The name matches one of the potential outcomes. There are no conflicting sources."
    reasoning_single_source_election = "The URL is a valid news page. The election has occurred, and Donald Trump is reported to have won with 312 votes. The rule specifies that the outcome is based on official election results."
    reasoning_all_sources_election = "The only data source provided is from BBC News. It reports that Donald Trump won the 2024 US presidential election with 312 votes. The rule specifies that the outcome is based on official election results. There are no other sources contradicting this information."

    mock_response = {
        "response": {
            f"outcomes.\n\n### Inputs\n<title>\n{markets_data[0]['title']}": json.dumps(
                {
                    "valid_source": "true",
                    "event_has_occurred": "true",
                    "reasoning": reasoning_single_source_marathon,
                    "outcome": markets_data[0]["outcome"],
                }
            ),
            f"inputs\n\n    ### Inputs\n    <title>\n    {markets_data[0]['title']}\n": json.dumps(
                {
                    "relevant_sources": [markets_data[0]["evidence_urls"]],
                    "reasoning": reasoning_all_sources_marathon,
                    "outcome": markets_data[0]["outcome"],
                }
            ),
            f"outcomes.\n\n### Inputs\n<title>\n{markets_data[1]['title']}": json.dumps(
                {
                    "valid_source": "true",
                    "event_has_occurred": "true",
                    "reasoning": reasoning_single_source_election,
                    "outcome": markets_data[1]["outcome"],
                }
            ),
            f"inputs\n\n    ### Inputs\n    <title>\n    {markets_data[1]['title']}\n": json.dumps(
                {
                    "relevant_sources": [markets_data[1]["evidence_urls"]],
                    "reasoning": reasoning_all_sources_election,
                    "outcome": markets_data[1]["outcome"],
                }
            ),
        },
        "eq_principle_prompt_comparative": {
            reasoning_single_source_marathon: True,
            reasoning_all_sources_marathon: True,
            reasoning_single_source_election: True,
            reasoning_all_sources_election: True,
        },
    }

    setup_validators(mock_response)

    # Get the intelligent oracle factory
    intelligent_oracle_factory = get_contract_factory("IntelligentOracle")

    # Deploy the Registry contract with the IntelligentOracle code
    registry_factory = get_contract_factory("Registry")
    registry_contract = registry_factory.deploy(
        args=[intelligent_oracle_factory.contract_code]
    )

    # Create markets through factory
    created_market_contracts = []
    for market_data in markets_data:
        create_result = registry_contract.create_new_prediction_market(
            args=[
                market_data["prediction_market_id"],
                market_data["title"],
                market_data["description"],
                market_data["potential_outcomes"],
                market_data["rules"],
                market_data["data_source_domains"],
                market_data["resolution_urls"],
                market_data["earliest_resolution_date"],
            ],
        )
        assert tx_execution_succeeded(create_result)

        # Get the latest contract address from factory
        registered_addresses = registry_contract.get_contract_addresses(args=[])
        new_market_address = registered_addresses[-1]

        # Build a contract object
        market_contract = intelligent_oracle_factory.build_contract(new_market_address)
        created_market_contracts.append(market_contract)

        # Wait for the new market contract to be deployed
        assert wait_for_contract_deployment(
            market_contract
        ), f"Market contract deployment timeout for {market_data['prediction_market_id']}"

    # Verify all markets were registered
    assert len(registered_addresses) == len(markets_data)

    # Verify each market's state
    for i, market_contract in enumerate(created_market_contracts):
        market_state = market_contract.get_dict(args=[])
        expected_data = markets_data[i]

        # Verify key market properties
        assert market_state["title"] == expected_data["title"]
        assert market_state["description"] == expected_data["description"]
        assert market_state["potential_outcomes"] == expected_data["potential_outcomes"]
        assert market_state["rules"] == expected_data["rules"]
        assert (
            market_state["data_source_domains"] == expected_data["data_source_domains"]
        )
        assert market_state["resolution_urls"] == expected_data["resolution_urls"]
        assert market_state["status"] == "Active"
        assert (
            market_state["earliest_resolution_date"]
            == expected_data["earliest_resolution_date"]
        )
        assert (
            market_state["prediction_market_id"]
            == expected_data["prediction_market_id"]
        )

    # Resolve markets
    for i, market_contract in enumerate(created_market_contracts):
        resolve_result = market_contract.resolve(
            args=[markets_data[i]["evidence_urls"]],
        )
        assert tx_execution_succeeded(resolve_result)

        # Verify market was resolved and has the correct outcome
        market_state = market_contract.get_dict(args=[])
        assert market_state["status"] == "Resolved"
        assert market_state["outcome"] == markets_data[i]["outcome"]
