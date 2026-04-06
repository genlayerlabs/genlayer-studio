from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from gltest.types import TransactionStatus
from eth_account import Account

from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status


def _get_eoa_balance(address: str) -> int:
    result = post_request_localhost(payload("eth_getBalance", address)).json()
    assert has_success_status(result)
    return (
        int(result["result"], 16)
        if isinstance(result["result"], str)
        else result["result"]
    )


def test_faucet_send_to_eoa(setup_validators):
    """Faucet contract sends value to an EOA via EthSend (external message)."""
    setup_validators()

    factory = get_contract_factory(
        contract_file_path="tests/integration/icontracts/contracts/faucet.py"
    )
    faucet = factory.deploy()

    # Create a fresh EOA recipient
    eoa = Account.create()

    # Fund the faucet by calling send to the EOA with value
    tx = faucet.send(args=[eoa.address]).transact(
        value=1000,
        wait_transaction_status=TransactionStatus.FINALIZED,
        wait_triggered_transactions=True,
        wait_triggered_transactions_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(tx)

    # Faucet should have forwarded all value — balance should be 0
    assert faucet.get_balance(args=[]).call() == 0

    # EOA should have received the value
    eoa_balance = _get_eoa_balance(eoa.address)
    assert eoa_balance == 1000


def test_faucet_send_to_contract(setup_validators):
    """Faucet contract sends value to another contract via EthSend."""
    setup_validators()

    factory = get_contract_factory(
        contract_file_path="tests/integration/icontracts/contracts/faucet.py"
    )
    faucet = factory.deploy()
    target = factory.deploy()

    tx = faucet.send(args=[target.address]).transact(
        value=500,
        wait_transaction_status=TransactionStatus.FINALIZED,
        wait_triggered_transactions=True,
        wait_triggered_transactions_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_succeeded(tx)

    assert faucet.get_balance(args=[]).call() == 0
    assert target.get_balance(args=[]).call() == 500
