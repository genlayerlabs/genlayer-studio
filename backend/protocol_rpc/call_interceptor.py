from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc.calls_intercept.get_latest_pending_tx_count import (
    GetLatestPendingTxCountHandler,
)

CONSENSUS_DATA_CONTRACT_ADDRESS = "0x88B0F18613Db92Bf970FfE264E02496e20a74D16"

# Registry of available handler instances
HANDLERS = [
    GetLatestPendingTxCountHandler(),
]


def is_consensus_data_contract_call(to_address: str) -> bool:
    """Check if the eth_call is targeting the ConsensusData contract."""
    return to_address.lower() == CONSENSUS_DATA_CONTRACT_ADDRESS.lower()


def handle_consensus_data_call(
    transactions_processor: TransactionsProcessor, to_address: str, data: str
) -> str | None:
    """Handle ConsensusData contract calls by intercepting and processing locally."""
    if not is_consensus_data_contract_call(to_address):
        return None

    for handler in HANDLERS:
        if handler.can_handle(data):
            return handler.handle(transactions_processor, data)

    return None
