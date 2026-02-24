from gltest import get_contract_factory, create_account
from gltest.assertions import tx_execution_succeeded


def test_multi_read_erc20(setup_validators):
    """
    This test verifies the functionality of a multi-read ERC20 contract. It deploys two separate ERC20 token contracts
    (referred to as 'doge' and 'shiba') and a multi-read ERC20 contract. The test aims to:

    1. Deploy two different ERC20 token contracts with a total supply of 1000 tokens each.
    2. Deploy a multi-read ERC20 contract that can interact with multiple ERC20 tokens.
    3. Test the ability of the multi-read contract to update and retrieve token balances for multiple ERC20 tokens
       and multiple accounts simultaneously.
    4. Ensure the multi-read contract correctly maintains and reports balances for different account-token combinations.

    This test demonstrates the integration contract to contract reads
    """
    setup_validators()
    TOKEN_TOTAL_SUPPLY = 1000
    from_account_doge = create_account()
    from_account_shiba = create_account()

    # LLM ERC20
    llm_erc20_factory = get_contract_factory("LlmErc20")

    ## Deploy first LLM ERC20 Contract
    doge_contract = llm_erc20_factory.deploy(
        args=[TOKEN_TOTAL_SUPPLY], account=from_account_doge
    )

    ## Deploy second LLM ERC20 Contract
    shiba_contract = llm_erc20_factory.deploy(
        args=[TOKEN_TOTAL_SUPPLY], account=from_account_shiba
    )

    # Deploy Multi Read ERC20 Contract
    multi_read_erc20_factory = get_contract_factory("multi_read_erc20")

    multi_read_contract = multi_read_erc20_factory.deploy(
        args=[], account=from_account_doge
    )

    # update balances for doge account
    transaction_response_call = multi_read_contract.update_token_balances(
        args=[
            from_account_doge.address,
            [doge_contract.address, shiba_contract.address],
        ]
    ).transact()
    assert tx_execution_succeeded(transaction_response_call)

    # check balances
    call_method_response_get_balances = multi_read_contract.get_balances(args=[]).call()
    assert call_method_response_get_balances == {
        from_account_doge.address: {
            doge_contract.address: TOKEN_TOTAL_SUPPLY,
            shiba_contract.address: 0,
        }
    }

    # update balances for shiba account
    transaction_response_call = (
        multi_read_contract.connect(from_account_shiba)
        .update_token_balances(
            args=[
                from_account_shiba.address,
                [doge_contract.address, shiba_contract.address],
            ]
        )
        .transact()
    )
    assert tx_execution_succeeded(transaction_response_call)

    # check balances
    call_method_response_get_balances = (
        multi_read_contract.connect(from_account_shiba).get_balances(args=[]).call()
    )

    assert call_method_response_get_balances == {
        from_account_doge.address: {
            doge_contract.address: TOKEN_TOTAL_SUPPLY,
            shiba_contract.address: 0,
        },
        from_account_shiba.address: {
            doge_contract.address: 0,
            shiba_contract.address: TOKEN_TOTAL_SUPPLY,
        },
    }
