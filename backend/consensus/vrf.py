from datetime import datetime
import numpy as np
from backend.consensus.base import DEFAULT_VALIDATORS_COUNT


def get_validators_for_transaction(
    nodes: list[dict],
    num_validators: int | None = None,
    rng=np.random.default_rng(seed=int(datetime.now().timestamp())),
) -> list[dict]:
    """
    Returns subset of validators for a transaction.
    The selelction and order is given by a random sampling based on the stake of the validators.
    """
    if num_validators is None:
        num_validators = DEFAULT_VALIDATORS_COUNT

    num_validators = min(num_validators, len(nodes))

    total_stake = sum(validator["stake"] for validator in nodes)
    if total_stake == 0:
        # All validators have zero stake (e.g. internally-created validators in
        # protocol_rpc/endpoints.py). Dividing by zero crashed the consensus
        # worker, so fall back to a uniform distribution across equal-stake nodes.
        probabilities = [1 / len(nodes)] * len(nodes)
    else:
        probabilities = [validator["stake"] / total_stake for validator in nodes]

    selected_validators = rng.choice(
        nodes,
        p=probabilities,
        size=num_validators,
        replace=False,
    )

    return list(selected_validators)
