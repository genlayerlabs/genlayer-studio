from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from gltest.types import TransactionStatus


def test_balance_funded_descendant_grant_policies(setup_validators):
    setup_validators()
    factory = get_contract_factory(
        contract_file_path=(
            "tests/integration/icontracts/contracts/descendant_grants.py"
        )
    )
    relay = factory.deploy()
    target = factory.deploy()

    funding = relay.fund(args=[]).transact(
        value=10**23,
        wait_transaction_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(funding)

    closed = relay.emit_closed(args=[relay.address, target.address]).transact(
        wait_transaction_status=TransactionStatus.FINALIZED,
        wait_triggered_transactions=True,
        wait_triggered_transactions_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(closed)
    # The relay fails on its sender-funded emission, so its write rolls back
    assert relay.get_last(args=[]).call() == ""
    assert target.get_last(args=[]).call() == ""

    closed_balance_funded = relay.emit_closed_balance_funded(
        args=[relay.address, target.address]
    ).transact(
        wait_transaction_status=TransactionStatus.FINALIZED,
        wait_triggered_transactions=True,
        wait_triggered_transactions_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(closed_balance_funded)
    assert relay.get_last(args=[]).call() == "closed-balance-funded"
    assert target.get_last(args=[]).call() == "closed-balance-funded"

    opened = relay.emit_open(args=[relay.address, target.address]).transact(
        wait_transaction_status=TransactionStatus.FINALIZED,
        wait_triggered_transactions=True,
        wait_triggered_transactions_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(opened)
    assert target.get_last(args=[]).call() == "open"

    pinned = relay.emit_pinned(args=[relay.address, target.address]).transact(
        wait_transaction_status=TransactionStatus.FINALIZED,
        wait_triggered_transactions=True,
        wait_triggered_transactions_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(pinned)
    assert target.get_last(args=[]).call() == "pinned"
