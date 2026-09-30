import numpy as np
from numpy.random import Generator as RNG

from backend.consensus.constants import DEFAULT_VALIDATORS_COUNT
from backend.consensus.errors import NoValidatorsAvailableError


def get_validators_for_transaction(
    nodes: list[dict],
    num_validators: int | None = None,
    rng: RNG | None = None,
) -> list[dict]:
    """
    Returns subset of validators for a transaction.
    The selelction and order is given by a random sampling based on the stake of the validators.
    """
    if rng is None:
        rng = np.random.default_rng()

    if num_validators is None:
        num_validators = DEFAULT_VALIDATORS_COUNT

    num_validators = min(num_validators, len(nodes))

    total_stake = sum(validator["stake"] for validator in nodes)
    if total_stake <= 0:
        raise NoValidatorsAvailableError(
            "Cannot select validators: total validator stake must be positive."
        )
    probabilities = [validator["stake"] / total_stake for validator in nodes]

    selected_validators = rng.choice(
        nodes,
        p=probabilities,
        size=num_validators,
        replace=False,
    )

    return list(selected_validators)
