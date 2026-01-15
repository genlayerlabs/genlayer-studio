from gltest import get_contract_factory


def test_read_erc20(setup_validators, default_account):
    """
    Tests that recursive contract calls work by:
    1. creating an LLM ERC20 contract
    2. creating a read_erc20 contract that reads the LLM ERC20 contract
    3. creating a read_erc20 contract that reads the previous read_erc20 contract
    Repeats step 3 a few times.

    It's like a linked list, but with contracts.
    """
    setup_validators()
    TOKEN_TOTAL_SUPPLY = 1000

    # LLM ERC20
    llm_erc20_factory = get_contract_factory("LlmErc20")

    # Deploy Contract
    llm_erc20_contract = llm_erc20_factory.deploy(args=[TOKEN_TOTAL_SUPPLY])
    last_contract_address = llm_erc20_contract.address

    # Read ERC20
    read_erc20_factory = get_contract_factory("read_erc20")

    for i in range(5):
        print(f"Deploying contract, iteration {i}")

        # deploy contract
        read_erc20_contract = read_erc20_factory.deploy(args=[last_contract_address])
        last_contract_address = read_erc20_contract.address

        # check balance
        contract_state = read_erc20_contract.get_balance_of(
            args=[default_account.address]
        ).call()
        assert contract_state == TOKEN_TOTAL_SUPPLY
