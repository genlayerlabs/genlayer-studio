from unittest.mock import Mock

import pytest

from backend.consensus.errors import NoValidatorsAvailableError
from backend.consensus.vrf import get_validators_for_transaction


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


def test_get_validators_for_transaction_rejects_non_positive_total_stake():
    nodes = [{"stake": 0}, {"stake": 0}, {"stake": 0}]

    with pytest.raises(NoValidatorsAvailableError, match="must be positive"):
        get_validators_for_transaction(nodes, 2)


def test_get_validators_for_transaction_creates_rng_per_call(monkeypatch):
    nodes = [{"stake": 1}, {"stake": 2}]
    first_rng = Mock()
    second_rng = Mock()
    first_rng.choice.return_value = [nodes[0]]
    second_rng.choice.return_value = [nodes[1]]
    default_rng = Mock(side_effect=[first_rng, second_rng])
    monkeypatch.setattr("backend.consensus.vrf.np.random.default_rng", default_rng)

    assert get_validators_for_transaction(nodes, 1) == [nodes[0]]
    assert get_validators_for_transaction(nodes, 1) == [nodes[1]]

    assert default_rng.call_count == 2
    first_rng.choice.assert_called_once()
    second_rng.choice.assert_called_once()
