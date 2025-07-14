import json
from eth_account import Account
from backend.config.hardhat_config import HardhatConfig


def test_eth_account():
    """
    Test the creation and recovery of an Ethereum account using the eth_account library.

    This test performs the following steps:
    1. Creates a new Ethereum account.
    2. Connects to the Hardhat network.
    3. Asserts that the balance of the new account is zero.
    4. Asserts that the account address and private key are strings.
    5. Recovers the account from the private key.
    6. Asserts that the recovered account's private key and address match the original account.
    """
    account = Account.create()

    web3 = connect_to_hardhat()
    assert 0 == web3.eth.get_balance(account.address)
    assert isinstance(account.address, str)
    assert isinstance(account.key.hex(), str)

    recover_account = Account.from_key(account.key)
    assert recover_account.key.hex() == account.key.hex()
    assert recover_account.address == account.address


def connect_to_hardhat():
    """
    Connect to the Hardhat network.

    This function establishes a connection to the Hardhat network using the Web3 library.
    It raises an exception if the connection fails.

    Returns:
        Web3: An instance of the Web3 class connected to the Hardhat network.

    Raises:
        Exception: If the connection to the Hardhat network fails.
    """
    web3 = HardhatConfig.get_web3_instance()

    # Check connection
    if not web3.is_connected():
        raise Exception("Failed to connect to Hardhat Network")
    return web3


def test_hardhat():
    """
    Test the deployment and interaction with a smart contract on the Hardhat network.

    This test performs the following steps:
    1. Connects to the Hardhat network.
    2. Retrieves the first account and its private key.
    3. Reads the ABI and bytecode of the GhostContract from a JSON file.
    4. Asserts the ABI structure to ensure it matches the expected format.
    5. Creates a contract instance using the ABI and bytecode.
    6. Builds, signs, and sends a transaction to deploy the contract.
    7. Waits for the transaction receipt and asserts the sender's address.
    8. Retrieves the contract address from the receipt.
    9. Builds, signs, and sends a rollup transaction to the deployed contract.
    10. Waits for the transaction receipt and asserts the sender and receiver addresses.
    11. Retrieves the transaction details and asserts the input data.
    12. Asserts that the account balance remains unchanged after the transactions.
    """
    web3 = connect_to_hardhat()

    account = web3.eth.accounts[0]
    private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    account_balance = web3.eth.get_balance(account)

    # Create ghost contract
    # Read contract ABI and bytecode from compiled contract
    with open(
        "hardhat/artifacts/contracts/GhostContract.sol/GhostContract.json", "r"
    ) as f:
        contract_json = json.loads(f.read())
        abi = contract_json["abi"]
        bytecode = contract_json["bytecode"]
    assert abi == [
        {
            "anonymous": False,
            "inputs": [
                {
                    "indexed": False,
                    "internalType": "bytes",
                    "name": "data",
                    "type": "bytes",
                }
            ],
            "name": "ReceivedData",
            "type": "event",
        },
        {"stateMutability": "payable", "type": "fallback"},
    ]

    # Create the contract instance
    contact = web3.eth.contract(abi=abi, bytecode=bytecode)

    # Build the transaction
    gas_estimate = web3.eth.estimate_gas(
        contact.constructor().build_transaction(
            {
                "from": account,
                "nonce": web3.eth.get_transaction_count(account),
                "gasPrice": 0,
            }
        )
    )
    transaction = contact.constructor().build_transaction(
        {
            "from": account,
            "nonce": web3.eth.get_transaction_count(account),
            "gas": gas_estimate,
            "gasPrice": 0,
        }
    )

    # Sign the transaction
    signed_tx = web3.eth.account.sign_transaction(transaction, private_key=private_key)

    # Send the transaction
    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

    # Wait for the transaction receipt
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["from"] == account
    contract_address = receipt.contractAddress

    # Create rollup transaction
    gas_estimate = web3.eth.estimate_gas(
        {
            "from": account,
            "to": contract_address,
            "value": 0,
            "data": web3.to_hex(text="This will be genlayer data.."),
        }
    )

    transaction = {
        "from": account,
        "to": contract_address,
        "value": 0,
        "data": web3.to_hex(text="This will be genlayer data.."),
        "nonce": web3.eth.get_transaction_count(account),
        "gas": gas_estimate,
        "gasPrice": 0,
    }

    # Sign and send the transaction
    signed_tx = web3.eth.account.sign_transaction(transaction, private_key=private_key)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

    # Wait for transaction to be actually mined and get the receipt
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["from"] == account
    assert receipt["to"] == contract_address

    # Get full transaction details including input data
    transaction = web3.eth.get_transaction(tx_hash)
    assert "This will be genlayer data.." == web3.to_text(transaction["input"])

    # Check free transactions
    assert account_balance == web3.eth.get_balance(account)
