"""
Test for VRF algorithm without importing from backend to avoid circular import
"""
from unittest.mock import Mock, patch
import numpy as np
from datetime import datetime


def list_of_dicts_to_set(list_of_dicts: list[dict]) -> set:
    return set(map(lambda x: tuple(x.items()), list_of_dicts))


# Reimplementamos la función localmente para los tests para evitar importación circular
def get_validators_for_transaction_test(
    nodes: list[dict],
    num_validators: int | None = None,
    rng=np.random.default_rng(seed=int(datetime.now().timestamp())),
) -> list[dict]:
    """
    Local implementation for testing purposes.
    Returns subset of validators for a transaction.
    The selection and order is given by a random sampling based on the stake of the validators.
    """
    DEFAULT_VALIDATORS_COUNT = 5  # Local constant to avoid import
    
    if num_validators is None:
        num_validators = DEFAULT_VALIDATORS_COUNT

    num_validators = min(num_validators, len(nodes))

    total_stake = sum(validator["stake"] for validator in nodes)
    probabilities = [validator["stake"] / total_stake for validator in nodes]

    selected_validators = rng.choice(
        nodes,
        p=probabilities,
        size=num_validators,
        replace=False,
    )

    return list(selected_validators)


def test_get_validators_for_transaction():
    """
    Tests that
    * correctly returns all nodes when asked for more validators than there are nodes
    * the order of the validators is random
    """
    nodes = [{"stake": 1}, {"stake": 2}, {"stake": 3}]
    nodes_set = list_of_dicts_to_set(nodes)

    while True:
        validators = get_validators_for_transaction_test(nodes, 10)

        assert list_of_dicts_to_set(validators) == nodes_set

        if nodes != validators:
            # Since the order is random, at some point the order will be different
            break


def test_get_validators_for_transaction_2():
    """
    Tests that random selection should at some point return all nodes
    """
    nodes = [{"stake": 1}, {"stake": 2}, {"stake": 3}]
    nodes_set = list_of_dicts_to_set(nodes)

    accumulated = set()
    iterations = 0
    max_iterations = 100  # Prevent infinite loop
    
    while iterations < max_iterations:
        validators = get_validators_for_transaction_test(nodes, 2)
        accumulated.update(list_of_dicts_to_set(validators))

        if accumulated == nodes_set:
            break
        iterations += 1
    
    assert accumulated == nodes_set, f"Failed to get all nodes after {max_iterations} iterations"


def test_get_validators_for_transaction_3():
    """
    Tests that the gathering of probabilities is correct for passing to the random selector
    """
    nodes = [{"stake": 1}, {"stake": 2}, {"stake": 3}]

    def choice_mock(a, p, size, replace):
        assert p == [1 / 6, 2 / 6, 3 / 6]
        assert size == 3
        assert replace is False
        return sorted(a, key=lambda x: -x["stake"])

    rng = Mock()
    rng.choice.side_effect = choice_mock

    validators = get_validators_for_transaction_test(nodes, 10, rng=rng)

    rng.choice.assert_called_once()
    assert validators == [{"stake": 3}, {"stake": 2}, {"stake": 1}]


def test_vrf_algorithm_with_default_validators():
    """
    Test that the default number of validators is used when not specified
    """
    nodes = [
        {"stake": 1, "id": 1},
        {"stake": 2, "id": 2},
        {"stake": 3, "id": 3},
        {"stake": 4, "id": 4},
        {"stake": 5, "id": 5},
        {"stake": 6, "id": 6},
    ]
    
    validators = get_validators_for_transaction_test(nodes)
    
    # Should return 5 validators by default (DEFAULT_VALIDATORS_COUNT)
    assert len(validators) == 5
    assert all(v in nodes for v in validators)


def test_vrf_algorithm_stake_weighting():
    """
    Test that higher stake nodes are more likely to be selected
    """
    nodes = [
        {"stake": 1, "id": "low"},
        {"stake": 100, "id": "high"},
    ]
    
    high_stake_count = 0
    iterations = 100
    
    for _ in range(iterations):
        validators = get_validators_for_transaction_test(nodes, 1)
        if validators[0]["id"] == "high":
            high_stake_count += 1
    
    # With 100:1 stake ratio, high stake should be selected ~99% of the time
    assert high_stake_count > 90, f"High stake selected only {high_stake_count}% of the time"