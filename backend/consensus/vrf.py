import secrets
import numpy as np
from backend.consensus.base import DEFAULT_VALIDATORS_COUNT


def get_validators_for_transaction(
    nodes: list[dict],
    num_validators: int | None = None,
    rng=None,
) -> list[dict]:
    """
    Returns subset of validators for a transaction.
    The selelction and order is given by a random sampling based on the stake of the validators.
    """
    # Seed per call from OS entropy. A module-level default_rng evaluated at
    # import time is shared process-wide and seeded from wall-clock *seconds*,
    # so separate processes started in the same second (RPC + workers under
    # docker compose) get identical, predictable streams.
    if rng is None:
        rng = np.random.default_rng(seed=secrets.randbits(128))

    if num_validators is None:
        num_validators = DEFAULT_VALIDATORS_COUNT

    num_validators = min(num_validators, len(nodes))

    total_stake = sum(validator["stake"] for validator in nodes)
    if total_stake <= 0:
        # Every validator has zero stake: fall back to uniform selection
        # instead of dividing by zero.
        probabilities = None
    else:
        probabilities = [validator["stake"] / total_stake for validator in nodes]
        # rng.choice(replace=False) raises if there are fewer positive-weight
        # entries than draws requested. When we must select more validators
        # than have positive stake, zero-stake validators have to be included,
        # so weighting is impossible — fall back to uniform.
        if sum(1 for p in probabilities if p > 0) < num_validators:
            probabilities = None

    selected_validators = rng.choice(
        nodes,
        p=probabilities,
        size=num_validators,
        replace=False,
    )

    return list(selected_validators)
