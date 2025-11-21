from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from gltest.types import TransactionStatus


def test_deploy(setup_validators):
    setup_validators()
    factory = get_contract_factory("MultiFileContract")
    contract = factory.deploy(
        args=[],
        wait_transaction_status=TransactionStatus.FINALIZED,
        wait_triggered_transactions=True,
        wait_triggered_transactions_status=TransactionStatus.ACCEPTED,
    )

    res = contract.test(args=[]).call()
    assert res == "123"
