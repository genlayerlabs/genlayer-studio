from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from gltest.types import TransactionStatus


def test_payable_deposit(setup_validators):
    """Basic payable: deposit value and verify state + balance."""
    setup_validators()
    factory = get_contract_factory(
        contract_file_path="tests/integration/icontracts/contracts/payable_escrow.py"
    )
    contract = factory.deploy()

    tx = contract.deposit(args=[]).transact(
        value=500,
        wait_transaction_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(tx)

    assert contract.get_deposited(args=[]).call() == 500
    assert contract.get_balance(args=[]).call() == 500


def test_payable_accumulation(setup_validators):
    """Multiple deposits accumulate correctly."""
    setup_validators()
    factory = get_contract_factory(
        contract_file_path="tests/integration/icontracts/contracts/payable_escrow.py"
    )
    contract = factory.deploy()

    tx1 = contract.deposit(args=[]).transact(
        value=500,
        wait_transaction_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(tx1)

    tx2 = contract.deposit(args=[]).transact(
        value=300,
        wait_transaction_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(tx2)

    assert contract.get_deposited(args=[]).call() == 800
    assert contract.get_balance(args=[]).call() == 800


def test_payable_zero_value_rejected(setup_validators):
    """Calling deposit without value raises UserError."""
    setup_validators()
    factory = get_contract_factory(
        contract_file_path="tests/integration/icontracts/contracts/payable_escrow.py"
    )
    contract = factory.deploy()

    tx = contract.deposit(args=[]).transact(
        value=0,
        wait_transaction_status=TransactionStatus.FINALIZED,
    )
    assert not tx_execution_succeeded(tx)


def test_payable_withdraw_emit_transfer(setup_validators):
    """Withdraw uses emit_transfer to send value to a second contract."""
    setup_validators()
    factory = get_contract_factory(
        contract_file_path="tests/integration/icontracts/contracts/payable_escrow.py"
    )
    # Deploy source and target contracts
    source_contract = factory.deploy()
    target_contract = factory.deploy()

    # Deposit into source
    tx1 = source_contract.deposit(args=[]).transact(
        value=500,
        wait_transaction_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(tx1)

    # Withdraw from source to target (emit_transfer to a contract address)
    tx2 = source_contract.withdraw(args=[target_contract.address]).transact(
        wait_transaction_status=TransactionStatus.FINALIZED,
        wait_triggered_transactions=True,
        wait_triggered_transactions_status=TransactionStatus.ACCEPTED,
    )
    assert tx_execution_succeeded(tx2)

    # Source should be drained
    assert source_contract.get_deposited(args=[]).call() == 0
    assert source_contract.get_balance(args=[]).call() == 0

    # Target should have received the value
    assert target_contract.get_balance(args=[]).call() == 500

    # NOTE: test_payable_deploy_with_value deferred — gltest.deploy() doesn't accept value yet
