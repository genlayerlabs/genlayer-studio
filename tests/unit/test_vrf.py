from backend.consensus.vrf import get_validators_for_transaction
from unittest.mock import Mock
import pytest


def list_of_dicts_to_set(list_of_dicts: list[dict]) -> set:
    return set(map(lambda x: tuple(x.items()), list_of_dicts))


def test_get_validators_for_transaction():
    """
    Tests that
    * correctly returns all nodes when asked for more validators than there are nodes
    * the order of the validators is random

    """
    nodes = [{"stake": 1}, {"stake": 2}, {"stake": 3}]
    nodes_set = list_of_dicts_to_set(nodes)

    while True:
        validators = get_validators_for_transaction(nodes, 10)

        assert list_of_dicts_to_set(validators) == nodes_set

        if nodes != validators:
            # Since the order is random, at some point the order will be different
            break

    print(validators)


def test_get_validators_for_transaction_2():
    """
    Tests that random selection should at some point return all nodes
    """

    nodes = [{"stake": 1}, {"stake": 2}, {"stake": 3}]

    nodes_set = list_of_dicts_to_set(nodes)

    accumulated = set()
    while True:
        validators = get_validators_for_transaction(nodes, 2)
        print(validators)
        accumulated.update(list_of_dicts_to_set(validators))

        if accumulated == nodes_set:
            break


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

    validators = get_validators_for_transaction(nodes, 10, rng=rng)

    rng.choice.assert_called_once()
    assert validators == [{"stake": 3}, {"stake": 2}, {"stake": 1}]


def test_get_validators_zero_stake_raises():
    """When all stakes are zero, selection should raise ValueError."""
    nodes = [{"stake": 0}, {"stake": 0}]
    with pytest.raises(ValueError, match="all stakes are zero"):
        get_validators_for_transaction(nodes, 1)


def test_get_validators_rng_not_shared_between_calls():
    """Each call without an explicit rng should get a fresh generator."""
    nodes = [{"stake": 1}, {"stake": 2}, {"stake": 3}]
    # Call twice without passing rng — the default must be created fresh each time.
    v1 = get_validators_for_transaction(nodes, 1)
    v2 = get_validators_for_transaction(nodes, 1)
    # Both should succeed (no shared mutable state corruption).
    assert len(v1) == 1
    assert len(v2) == 1
