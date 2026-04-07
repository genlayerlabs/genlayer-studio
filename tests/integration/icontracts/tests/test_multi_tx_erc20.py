from gltest import get_contract_factory, create_account
from gltest.assertions import tx_execution_succeeded
import json

TOKEN_TOTAL_SUPPLY = 1000
TRANSFER_AMOUNT = 100


def _mock_transfer(sender_balance, recipient_balance, sender_addr, recipient_addr):
    """Build a mock LLM response for a successful ERC20 transfer."""
    return {
        "response": {
            "The balance of the sender": json.dumps(
                {
                    "transaction_success": True,
                    "transaction_error": "",
                    "updated_balances": {
                        sender_addr: sender_balance,
                        recipient_addr: recipient_balance,
                    },
                }
            )
        },
        "eq_principle_prompt_non_comparative": {"The balance of the sender": True},
    }


def test_multiple_transactions_same_contract(setup_validators, default_account):
    """Deploy an ERC20 contract and run multiple sequential transactions
    against it, verifying balances after each one.

    This covers the scenario described in issue #441: running multiple
    transactions against the same contract to catch race conditions or
    state corruption between consecutive writes.
    """
    account_a = default_account
    account_b = create_account()
    account_c = create_account()

    # --- Transaction 1: A -> B (100 tokens) ---
    mock_response_1 = _mock_transfer(
        sender_balance=TOKEN_TOTAL_SUPPLY - TRANSFER_AMOUNT,
        recipient_balance=TRANSFER_AMOUNT,
        sender_addr=account_a.address,
        recipient_addr=account_b.address,
    )
    setup_validators(mock_response_1)

    factory = get_contract_factory("LlmErc20")
    contract = factory.deploy(args=[TOKEN_TOTAL_SUPPLY])

    # Verify initial state
    initial_balance = contract.get_balance_of(args=[account_a.address]).call()
    assert initial_balance == TOKEN_TOTAL_SUPPLY

    tx1 = contract.transfer(args=[TRANSFER_AMOUNT, account_b.address]).transact()
    assert tx_execution_succeeded(tx1)

    balance_a_after_tx1 = contract.get_balance_of(args=[account_a.address]).call()
    balance_b_after_tx1 = contract.get_balance_of(args=[account_b.address]).call()
    assert balance_a_after_tx1 == TOKEN_TOTAL_SUPPLY - TRANSFER_AMOUNT
    assert balance_b_after_tx1 == TRANSFER_AMOUNT

    # --- Transaction 2: A -> C (200 tokens) ---
    mock_response_2 = _mock_transfer(
        sender_balance=TOKEN_TOTAL_SUPPLY - TRANSFER_AMOUNT - 2 * TRANSFER_AMOUNT,
        recipient_balance=2 * TRANSFER_AMOUNT,
        sender_addr=account_a.address,
        recipient_addr=account_c.address,
    )
    setup_validators(mock_response_2)

    tx2 = contract.transfer(args=[2 * TRANSFER_AMOUNT, account_c.address]).transact()
    assert tx_execution_succeeded(tx2)

    balance_a_after_tx2 = contract.get_balance_of(args=[account_a.address]).call()
    balance_c_after_tx2 = contract.get_balance_of(args=[account_c.address]).call()
    assert balance_a_after_tx2 == TOKEN_TOTAL_SUPPLY - 3 * TRANSFER_AMOUNT
    assert balance_c_after_tx2 == 2 * TRANSFER_AMOUNT

    # --- Transaction 3: B -> C (50 tokens) ---
    mock_response_3 = _mock_transfer(
        sender_balance=TRANSFER_AMOUNT - TRANSFER_AMOUNT // 2,
        recipient_balance=2 * TRANSFER_AMOUNT + TRANSFER_AMOUNT // 2,
        sender_addr=account_b.address,
        recipient_addr=account_c.address,
    )
    setup_validators(mock_response_3)

    tx3 = (
        contract.connect(account_b)
        .transfer(args=[TRANSFER_AMOUNT // 2, account_c.address])
        .transact()
    )
    assert tx_execution_succeeded(tx3)

    # --- Final balance verification ---
    balances = contract.get_balances(args=[]).call()

    expected_a = TOKEN_TOTAL_SUPPLY - 3 * TRANSFER_AMOUNT  # 1000 - 300 = 700
    expected_b = TRANSFER_AMOUNT - TRANSFER_AMOUNT // 2  # 100 - 50 = 50
    expected_c = 2 * TRANSFER_AMOUNT + TRANSFER_AMOUNT // 2  # 200 + 50 = 250

    assert balances[account_a.address] == expected_a
    assert balances[account_b.address] == expected_b
    assert balances[account_c.address] == expected_c

    # Total supply must be preserved across all transactions
    total = sum(balances.values())
    assert total == TOKEN_TOTAL_SUPPLY
