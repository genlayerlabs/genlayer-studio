import eth_utils
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc.calls_intercept import CallHandler
from flask_jsonrpc.exceptions import JSONRPCError
from sqlalchemy.exc import SQLAlchemyError


class GetLatestPendingTxCountHandler(CallHandler):
    """Handler for getLatestPendingTxCount method calls."""

    METHOD_SELECTOR = "fe4cfca7"

    def can_handle(self, data: str) -> bool:
        """Check if this handler can process the given call data."""
        if not data or len(data) < 10:
            return False

        method_selector = data[2:10] if data.startswith("0x") else data[:8]
        return method_selector.lower() == self.METHOD_SELECTOR.lower()

    def handle(self, transactions_processor: TransactionsProcessor, data: str) -> str:
        """Handle getLatestPendingTxCount call."""
        try:
            recipient_address = self._extract_recipient_address(data)
            count = transactions_processor.get_pending_transaction_count_for_address(
                recipient_address
            )

            result_bytes = count.to_bytes(32, byteorder="big")
            return eth_utils.hexadecimal.encode_hex(result_bytes)

        except ValueError as e:
            raise JSONRPCError(
                code=-32602,
                message="Invalid parameters for getLatestPendingTxCount",
                data={"error": str(e)},
            )
        except SQLAlchemyError as e:
            raise JSONRPCError(
                code=-32000,
                message="Database error querying pending transaction count",
                data={"error": str(e)},
            )
        except (OverflowError,) as e:
            raise JSONRPCError(
                code=-32000,
                message="Error formatting pending transaction count",
                data={"error": str(e)},
            )

    def _extract_recipient_address(self, data: str) -> str:
        """Extract recipient address from call data."""
        if not data:
            raise ValueError("Call data is empty")

        hex_data = data[2:] if data.startswith("0x") else data

        if len(hex_data) < 72:
            raise ValueError("Call data too short for getLatestPendingTxCount")

        address_param = hex_data[8:72]
        address_hex = address_param[-40:]

        return "0x" + address_hex
