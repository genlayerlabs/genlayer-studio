# RPC Call Interceptor - Handles specific contract calls locally instead of forwarding

import eth_utils
from backend.database_handler.transactions_processor import TransactionsProcessor
from flask_jsonrpc.exceptions import JSONRPCError
from sqlalchemy.exc import SQLAlchemyError

# ConsensusData contract configuration
CONSENSUS_DATA_CONTRACT_ADDRESS = "0x88B0F18613Db92Bf970FfE264E02496e20a74D16"
GET_LATEST_PENDING_TX_COUNT_SELECTOR = "fe4cfca7"
GET_LATEST_PENDING_TX_COUNT_SIGNATURE = "getLatestPendingTxCount(address)"


def is_consensus_data_contract_call(to_address: str) -> bool:
    """
    Check if the eth_call is targeting the ConsensusData contract.

    Args:
        to_address: The contract address being called

    Returns:
        bool: True if calling ConsensusData contract
    """
    return to_address.lower() == CONSENSUS_DATA_CONTRACT_ADDRESS.lower()


def is_get_latest_pending_tx_count_call(data: str) -> bool:
    """
    Check if the call data corresponds to getLatestPendingTxCount method.

    Args:
        data: The call data hex string

    Returns:
        bool: True if calling getLatestPendingTxCount method
    """
    if not data or len(data) < 10:  # Need at least method selector (0x + 8 chars)
        return False

    # Extract method selector (first 4 bytes after 0x)
    method_selector = data[2:10] if data.startswith("0x") else data[:8]
    return method_selector.lower() == GET_LATEST_PENDING_TX_COUNT_SELECTOR.lower()


def extract_recipient_address_from_call_data(data: str) -> str:
    """
    Extract the recipient address parameter from getLatestPendingTxCount call data.

    Args:
        data: The call data hex string

    Returns:
        str: The recipient address

    Raises:
        ValueError: If call data format is invalid
    """
    if not data:
        raise ValueError("Call data is empty")

    # Remove 0x prefix if present
    hex_data = data[2:] if data.startswith("0x") else data

    # Validate minimum length: 8 chars (method selector) + 64 chars (address parameter)
    if len(hex_data) < 72:
        raise ValueError("Call data too short for getLatestPendingTxCount")

    # Extract address parameter (skip 8 char method selector + 24 char padding)
    # Address is the last 20 bytes (40 hex chars) of the 32-byte parameter
    address_param = hex_data[8:72]  # Full 32-byte parameter
    address_hex = address_param[-40:]  # Last 20 bytes (40 hex chars)

    return "0x" + address_hex


def handle_get_latest_pending_tx_count(
    transactions_processor: TransactionsProcessor, recipient_address: str
) -> str:
    """
    Handle getLatestPendingTxCount call by querying local database.

    Args:
        transactions_processor: TransactionsProcessor instance
        recipient_address: The recipient address to count pending transactions for

    Returns:
        str: Hex-encoded uint256 result

    Raises:
        JSONRPCError: If database query fails
    """
    try:
        count = transactions_processor.get_pending_transaction_count_for_address(
            recipient_address
        )

        # Convert count to 32-byte big-endian hex string (uint256 format)
        result_bytes = count.to_bytes(32, byteorder="big")
        return eth_utils.hexadecimal.encode_hex(result_bytes)

    except SQLAlchemyError as e:
        raise JSONRPCError(
            code=-32000,
            message="Database error querying pending transaction count",
            data={"error": str(e), "recipient": recipient_address},
        ) from e
    except (ValueError, OverflowError) as e:
        raise JSONRPCError(
            code=-32000,
            message="Error formatting pending transaction count",
            data={"error": str(e), "recipient": recipient_address},
        ) from e


def handle_consensus_data_call(
    transactions_processor: TransactionsProcessor, to_address: str, data: str
) -> str | None:
    """
    Handle ConsensusData contract calls by intercepting and processing locally.

    Args:
        transactions_processor: TransactionsProcessor instance
        to_address: The contract address being called
        data: The call data

    Returns:
        str: Hex-encoded result if handled, None if not a ConsensusData call

    Raises:
        JSONRPCError: If the call fails
    """
    # Check if this is a ConsensusData contract call
    if not is_consensus_data_contract_call(to_address):
        return None

    # Check if this is getLatestPendingTxCount method
    if is_get_latest_pending_tx_count_call(data):
        try:
            recipient_address = extract_recipient_address_from_call_data(data)
            return handle_get_latest_pending_tx_count(
                transactions_processor, recipient_address
            )
        except ValueError as e:
            raise JSONRPCError(
                code=-32602,
                message="Invalid parameters for getLatestPendingTxCount",
                data={"error": str(e)},
            )

    # If it's a ConsensusData call but not a method we handle, return None
    # This will let it fall through to normal processing
    return None
