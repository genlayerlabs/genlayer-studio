import numpy as np
from numpy.random import Generator as RNG
from backend.consensus.base import DEFAULT_VALIDATORS_COUNT


def get_validators_for_transaction(
    nodes: list[dict],
    num_validators: int | None = None,
    rng: RNG | None = None,
) -> list[dict]:
    """
    Returns subset of validators for a transaction.
    The selelction and order is given by a random sampling based on the stake of the validators.

    Args:
        nodes: List of validator dicts, each with a "stake" key.
        num_validators: How many validators to select. Defaults to DEFAULT_VALIDATORS_COUNT.
        rng: Optional numpy Generator instance. A fresh default_rng() is created per call
             when not provided — avoids the mutable-default-argument pitfall where a single
             shared Generator would make the selection sequence predictable across calls.
    """
    if rng is None:
        rng = np.random.default_rng()

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
