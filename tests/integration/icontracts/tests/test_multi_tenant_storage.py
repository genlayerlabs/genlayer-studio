from gltest import get_contract_factory, create_account
from gltest.assertions import tx_execution_succeeded
from gltest.types import TransactionStatus


def test_multi_tenant_storage(setup_validators):
    """
    This test verifies the functionality of a multi-tenant storage contract. It deploys two separate storage contracts
    and a multi-tenant storage contract that manages them. The test aims to:

    1. Deploy two different storage contracts with initial storage values.
    2. Deploy a multi-tenant storage contract that can interact with multiple storage contracts.
    3. Test the ability of the multi-tenant contract to update and retrieve storage values for multiple users
       across different storage contracts.
    4. Ensure the multi-tenant contract correctly assigns users to storage contracts and manages their data.

    This test demonstrates contract-to-contract interactions and multi-tenant data management.
    """
    setup_validators()
    user_account_a = create_account()
    user_account_b = create_account()

    # Storage Contracts
    storage_factory = get_contract_factory(
        contract_file_path="examples/contracts/storage.py"
    )

    ## Deploy first Storage Contract
    first_storage_contract = storage_factory.deploy(args=["initial_storage_a"])

    ## Deploy second Storage Contract
    second_storage_contract = storage_factory.deploy(args=["initial_storage_b"])

    # Deploy Multi Tenant Storage Contract
    multi_tenant_storage_factory = get_contract_factory("MultiTentantStorage")
    multi_tenant_storage_contract = multi_tenant_storage_factory.deploy(
        args=[
            [
                first_storage_contract.address,
                second_storage_contract.address,
            ]
        ]
    )
    # update storage for first contract
    transaction_response_call = (
        multi_tenant_storage_contract.connect(user_account_a)
        .update_storage(args=["user_a_storage"])
        .transact(
            wait_transaction_status=TransactionStatus.FINALIZED,
            wait_triggered_transactions=True,
            wait_triggered_transactions_status=TransactionStatus.ACCEPTED,
        )
    )
    assert tx_execution_succeeded(transaction_response_call)

    # update storage for second contract
    transaction_response_call = (
        multi_tenant_storage_contract.connect(user_account_b)
        .update_storage(args=["user_b_storage"])
        .transact(
            wait_transaction_status=TransactionStatus.FINALIZED,
            wait_triggered_transactions=True,
            wait_triggered_transactions_status=TransactionStatus.ACCEPTED,
        )
    )
    assert tx_execution_succeeded(transaction_response_call)

    # get all storages
    storages = multi_tenant_storage_contract.get_all_storages(args=[]).call()

    assert storages == {
        second_storage_contract.address: "user_a_storage",
        first_storage_contract.address: "user_b_storage",
    }
