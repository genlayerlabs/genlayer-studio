# tests/e2e/test_storage.py

from tests.common.request import (
    payload,
    post_request_localhost,
    wait_for_transaction,
    send_transaction,
)


from tests.common.response import (
    has_success_status,
)
from gltest import create_account


def test_accounts_funding():
    account = create_account()
    new_account_address = account.address
    fund_amount = 1000

    # TODO: this is not working on CI https://github.com/yeagerai/genlayer-simulator/issues/548
    # Test fund_account with invalid address
    # invalid_address = "0xinvalid_address"
    # fund_invalid_account_result = post_request_localhost(
    #     payload("sim_fundAccount", invalid_address, fund_amount)
    # ).json()
    # assert has_error_status(fund_invalid_account_result)
    # print("fund_invalid_account_result", fund_invalid_account_result)

    # assert (
    #     "Incorrect address format. Please provide a valid address."
    #     in fund_invalid_account_result["error"]["message"]
    # )

    # Test fund_account
    fund_account_result = post_request_localhost(
        payload("sim_fundAccount", new_account_address, fund_amount)
    ).json()
    assert has_success_status(fund_account_result)
    wait_for_transaction(fund_account_result["result"])

    # Verify balance after funding
    get_balance_after_fund_result = post_request_localhost(
        payload("eth_getBalance", new_account_address)
    ).json()
    assert has_success_status(get_balance_after_fund_result)
    assert get_balance_after_fund_result["result"] == fund_amount

    # TODO: this is not working on CI https://github.com/yeagerai/genlayer-simulator/issues/548
    # Test get_balance with invalid address
    # get_balance_invalid_result = post_request_localhost(
    #     payload("eth_getBalance", invalid_address)
    # ).json()
    # assert has_error_status(get_balance_invalid_result)
    # assert (
    #     "Incorrect address format. Please provide a valid address."
    #     in fund_invalid_account_result["error"]["message"]
    # )


def test_accounts_transfers():
    # Setup test accounts
    account_1 = create_account()
    account_1_address = account_1.address

    account_2 = create_account()
    account_2_address = account_2.address

    fund_amount = 1000
    fund_account_result = post_request_localhost(
        payload("sim_fundAccount", account_1_address, fund_amount)
    ).json()
    wait_for_transaction(fund_account_result["result"])

    # Test transfer
    transfer_amount = 200
    transaction_response_call_1 = send_transaction(
        account_1, account_2.address, transfer_amount
    )
    assert has_success_status(transaction_response_call_1)

    # Verify balance after transfer
    get_balance_1_after_transfer = post_request_localhost(
        payload("eth_getBalance", account_1_address)
    ).json()
    assert has_success_status(get_balance_1_after_transfer)
    assert get_balance_1_after_transfer["result"] == fund_amount - transfer_amount

    get_balance_2_after_transfer = post_request_localhost(
        payload("eth_getBalance", account_2_address)
    ).json()
    assert has_success_status(get_balance_2_after_transfer)
    assert get_balance_2_after_transfer["result"] == transfer_amount


def test_accounts_burn():
    # Setup test accounts
    account_1 = create_account()
    account_1_address = account_1.address

    fund_amount = 1000
    fund_account_result = post_request_localhost(
        payload("sim_fundAccount", account_1_address, fund_amount)
    ).json()
    wait_for_transaction(fund_account_result["result"])

    # Test burn
    burn_amount = 200
    transaction_response_call_1 = send_transaction(account_1, None, burn_amount)
    assert has_success_status(transaction_response_call_1)

    # Verify balance after transfer
    get_balance_1_after_transfer = post_request_localhost(
        payload("eth_getBalance", account_1_address)
    ).json()
    assert has_success_status(get_balance_1_after_transfer)
    assert get_balance_1_after_transfer["result"] == fund_amount - burn_amount
