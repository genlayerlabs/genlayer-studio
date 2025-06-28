from eth_account import Account
from eth_utils import to_hex
import rlp
from eth_account._utils.legacy_transactions import Transaction


def serialize_one(data: bytes | str) -> bytes:
    return to_hex(data)


def encode_transaction_data(data: list) -> str:
    """
    Encode transaction data using RLP encoding
    Returns hex string with '0x' prefix
    """
    serialized_data = rlp.encode(data)
    return to_hex(serialized_data)


def sign_transaction(
    account: Account, data: list = None, to: str = None, value: int = 0, nonce: int = 0
) -> dict:
    transaction = {
        "nonce": nonce,
        "gasPrice": 0,
        "gas": 20000000,
        "to": to,
        "value": value,
    }
    if data is not None:
        transaction["data"] = data

    signed_transaction = Account.sign_transaction(transaction, account.key)
    return to_hex(signed_transaction.raw_transaction)
