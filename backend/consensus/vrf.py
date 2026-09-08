from datetime import datetime
import numpy as np
from backend.consensus.base import DEFAULT_VALIDATORS_COUNT


def get_validators_for_transaction(
    nodes: list[dict],
    num_validators: int | None = None,
    rng: np.random.Generator | None = None,
) -> list[dict]:
    """
    Returns subset of validators for a transaction.
    The selelction and order is given by a random sampling based on the stake of the validators.
    """
    if rng is None:
        rng = np.random.default_rng(seed=int(datetime.now().timestamp()))

    if num_validators is None:
        num_validators = DEFAULT_VALIDATORS_COUNT

    num_validators = min(num_validators, len(nodes))

    total_stake = sum(validator["stake"] for validator in nodes)
    if total_stake == 0:
        raise ValueError(
            "Cannot select validators: all stakes are zero. "
            "At least one validator must have a positive stake."
        )
    probabilities = [validator["stake"] / total_stake for validator in nodes]

    selected_validators = rng.choice(
        nodes,
        p=probabilities,
        size=num_validators,
        replace=False,
    )

    return list(selected_validators)
