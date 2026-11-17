"""The studio-only `sim_config.genvm_executor_selector` deploy parameter must
reach the freshly created snapshot of the contract being deployed, and
nothing else.
"""

from backend.consensus.base import (
    contract_snapshot_factory,
    transaction_genvm_executor_selector,
)
from backend.domain.types import (
    SimConfig,
    Transaction,
    TransactionStatus,
    TransactionType,
)


CONTRACT_ADDRESS = "0xcontract"


def _deploy_transaction(sim_config: SimConfig | None) -> Transaction:
    return Transaction(
        hash="0xabc",
        status=TransactionStatus.PENDING,
        type=TransactionType.DEPLOY_CONTRACT,
        from_address="0xsender",
        to_address=CONTRACT_ADDRESS,
        data={"contract_code": "contract code"},
        value=0,
        sim_config=sim_config,
    )


def test_transaction_reroute_to_without_sim_config():
    assert transaction_genvm_executor_selector(_deploy_transaction(None)) is None


def test_transaction_reroute_to_from_sim_config():
    transaction = _deploy_transaction(
        SimConfig(validators=[], genvm_executor_selector="v0.2.17"),
    )
    assert transaction_genvm_executor_selector(transaction) == "v0.2.17"


def test_deploy_snapshot_carries_reroute_to():
    transaction = _deploy_transaction(
        SimConfig(validators=[], genvm_executor_selector="v0.2.17"),
    )
    snapshot = contract_snapshot_factory(CONTRACT_ADDRESS, None, transaction)
    assert snapshot.genvm_executor_selector == "v0.2.17"


def test_deploy_snapshot_without_reroute_to():
    snapshot = contract_snapshot_factory(
        CONTRACT_ADDRESS, None, _deploy_transaction(None)
    )
    assert snapshot.genvm_executor_selector is None
