# consensus/services/transactions_db_service.py
import json
import base64
import time
import os
from enum import Enum
import rlp
import re

from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, and_, cast, JSON, String, type_coerce, text
from sqlalchemy.orm.attributes import flag_modified
from eth_utils import to_bytes, keccak, is_address
from web3 import Web3

from backend.node.types import Receipt, ExecutionResultStatus
from .models import Transactions, TransactionStatus


class TransactionAddressFilter(Enum):
    ALL = "all"
    TO = "to"
    FROM = "from"


class TransactionsProcessor:
    def __init__(
        self,
        session: Session,
    ):
        self.session = session

        # Connect to Hardhat Network
        port = os.environ.get("HARDHAT_PORT")
        url = os.environ.get("HARDHAT_URL")
        hardhat_url = f"{url}:{port}"
        self.web3 = Web3(Web3.HTTPProvider(hardhat_url))

    @staticmethod
    def _parse_transaction_data(transaction_data: Transactions) -> dict:
        if transaction_data.consensus_data:
            leader_receipts = transaction_data.consensus_data.get("leader_receipt", [])
            if isinstance(leader_receipts, dict):
                result = leader_receipts.get("result", {})
            elif isinstance(leader_receipts, list) and len(leader_receipts) > 0:
                result = leader_receipts[0].get("result", {})
            else:
                result = {}
        else:
            result = transaction_data.consensus_data
        if isinstance(result, dict):
            result = result.get("raw", {})
        return {
            "hash": transaction_data.hash,
            "from_address": transaction_data.from_address,
            "to_address": transaction_data.to_address,
            "data": transaction_data.data,
            "value": transaction_data.value,
            "type": transaction_data.type,
            "status": transaction_data.status.value,
            "result": TransactionsProcessor._decode_base64_data(result),
            "consensus_data": transaction_data.consensus_data,
            "gaslimit": transaction_data.nonce,
            "nonce": transaction_data.nonce,
            "r": transaction_data.r,
            "s": transaction_data.s,
            "v": transaction_data.v,
            "created_at": transaction_data.created_at.isoformat(),
            "leader_only": transaction_data.leader_only,
            "triggered_by": transaction_data.triggered_by_hash,
            "triggered_transactions": [
                transaction.hash
                for transaction in transaction_data.triggered_transactions
            ],
            "appealed": transaction_data.appealed,
            "timestamp_awaiting_finalization": transaction_data.timestamp_awaiting_finalization,
            "appeal_failed": transaction_data.appeal_failed,
            "appeal_undetermined": transaction_data.appeal_undetermined,
            "consensus_history": transaction_data.consensus_history,
            "timestamp_appeal": transaction_data.timestamp_appeal,
            "appeal_processing_time": transaction_data.appeal_processing_time,
            "contract_snapshot": transaction_data.contract_snapshot,
            "config_rotation_rounds": transaction_data.config_rotation_rounds,
        }

    @staticmethod
    def _transaction_data_to_str(data: dict) -> str:
        """
        NOTE: json doesn't support bytes object, so they need to be encoded somehow
            Common approaches can be: array, hex string, base64 string
            Array takes a lot of space (extra comma for each element)
            Hex is double in size
            Base64 is 1.33 in size
            So base64 is chosen
        """

        def data_encode(d):
            if isinstance(d, bytes):
                return str(base64.b64encode(d), encoding="ascii")
            raise TypeError("Can't encode #{d}")

        return json.dumps(data, default=data_encode)

    @staticmethod
    def _decode_base64_data(data: dict | str) -> dict | str:
        def decode_value(value):
            """Helper function to decode Base64-encoded values if they are strings."""
            if (
                isinstance(value, str)
                and value
                and bool(re.compile(r"^[A-Za-z0-9+/]*={0,2}$").fullmatch(value)) is True
            ):
                try:
                    decoded_str = base64.b64decode(
                        bytes(value, encoding="utf-8")
                    ).decode("utf-8", errors="ignore")
                    byte_content = re.sub(r"^[\x00-\x1f]+", "", decoded_str)
                    if byte_content or len(byte_content) >= 0:
                        return byte_content
                    return decoded_str
                except (ValueError, UnicodeDecodeError):
                    return value  # Return original if decoding fails

            return value  # Return unchanged for non-strings

        if isinstance(data, dict):
            data = {k: decode_value(v) for k, v in data.items()}
            return data
        elif isinstance(data, str):
            data = decode_value(data)
            return data
        elif data is None:
            return None
        else:
            raise TypeError(f"Can't decode unsupported type: {type(data).__name__}")

    @staticmethod
    def _generate_transaction_hash(
        from_address: str,
        to_address: str,
        data: dict,
        value: float,
        type: int,
        nonce: int,
    ) -> str:
        from_address_bytes = (
            to_bytes(hexstr=from_address) if is_address(from_address) else None
        )
        to_address_bytes = (
            to_bytes(hexstr=to_address) if is_address(to_address) else None
        )

        data_bytes = to_bytes(text=TransactionsProcessor._transaction_data_to_str(data))

        tx_elements = [
            from_address_bytes,
            to_address_bytes,
            to_bytes(hexstr=hex(int(value))),
            data_bytes,
            to_bytes(hexstr=hex(type)),
            to_bytes(hexstr=hex(nonce)),
            to_bytes(hexstr=hex(0)),  # gas price (placeholder)
            to_bytes(hexstr=hex(0)),  # gas limit (placeholder)
        ]

        # Filter out None values
        tx_elements = [elem for elem in tx_elements if elem is not None]
        rlp_encoded = rlp.encode(tx_elements)
        hash = "0x" + keccak(rlp_encoded).hex()
        return hash

    def insert_transaction(
        self,
        from_address: str,
        to_address: str,
        data: dict,
        value: float,
        type: int,
        nonce: int,
        leader_only: bool,
        config_rotation_rounds: int,
        triggered_by_hash: (
            str | None
        ) = None,  # If filled, the transaction must be present in the database (committed)
        transaction_hash: str | None = None,
    ) -> str:
        current_nonce = self.get_transaction_count(from_address)

        # Follow up: https://github.com/MetaMask/metamask-extension/issues/29787
        # to uncomment this check
        # if nonce != current_nonce:
        #     raise Exception(
        #         f"Unexpected nonce. Provided: {nonce}, expected: {current_nonce}"
        #     )

        if transaction_hash is None:
            transaction_hash = self._generate_transaction_hash(
                from_address, to_address, data, value, type, current_nonce
            )

        new_transaction = Transactions(
            hash=transaction_hash,
            from_address=from_address,
            to_address=to_address,
            data=json.loads(self._transaction_data_to_str(data)),
            value=value,
            type=type,
            status=TransactionStatus.PENDING,
            consensus_data=None,  # Will be set when the transaction is finalized
            nonce=nonce,
            # Future fields, unused for now
            gaslimit=None,
            input_data=None,
            r=None,
            s=None,
            v=None,
            leader_only=leader_only,
            triggered_by=(
                self.session.query(Transactions).filter_by(hash=triggered_by_hash).one()
                if triggered_by_hash
                else None
            ),
            appealed=False,
            timestamp_awaiting_finalization=None,
            appeal_failed=0,
            appeal_undetermined=False,
            consensus_history={},
            timestamp_appeal=None,
            appeal_processing_time=0,
            contract_snapshot=None,
            config_rotation_rounds=config_rotation_rounds,
        )

        self.session.add(new_transaction)

        self.session.flush()  # So that `created_at` gets set

        return new_transaction.hash

    def get_transaction_by_hash(self, transaction_hash: str) -> dict | None:
        transaction = (
            self.session.query(Transactions)
            .filter_by(hash=transaction_hash)
            .one_or_none()
        )

        if transaction is None:
            return None

        return self._parse_transaction_data(transaction)

    def update_transaction_status(
        self,
        transaction_hash: str,
        new_status: TransactionStatus,
        update_current_status_changes: bool = True,
    ):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transaction.status = new_status

        if update_current_status_changes:
            if not transaction.consensus_history:
                transaction.consensus_history = {}

            if "current_status_changes" in transaction.consensus_history:
                transaction.consensus_history["current_status_changes"].append(
                    new_status.value
                )
            else:
                transaction.consensus_history["current_status_changes"] = [
                    TransactionStatus.PENDING.value,
                    new_status.value,
                ]
            flag_modified(transaction, "consensus_history")

        self.session.commit()

    def set_transaction_result(
        self, transaction_hash: str, consensus_data: dict | None
    ):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transaction.consensus_data = consensus_data
        self.session.commit()

    def get_transaction_count(self, address: str) -> int:
        count = (
            self.session.query(Transactions)
            .filter(Transactions.from_address == address)
            .count()
        )
        return count

    def get_transactions_for_address(
        self,
        address: str,
        filter: TransactionAddressFilter,
    ) -> list[dict]:
        query = self.session.query(Transactions)

        if filter == TransactionAddressFilter.TO:
            query = query.filter(Transactions.to_address == address)
        elif filter == TransactionAddressFilter.FROM:
            query = query.filter(Transactions.from_address == address)
        else:  # TransactionFilter.ALL
            query = query.filter(
                or_(
                    Transactions.from_address == address,
                    Transactions.to_address == address,
                )
            )

        transactions = query.order_by(Transactions.created_at.desc()).all()

        return [
            self._parse_transaction_data(transaction) for transaction in transactions
        ]

    def set_transaction_appeal(self, transaction_hash: str, appeal: bool):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        # You can only appeal the transaction if it is in accepted or undetermined state
        # Setting it to false is always allowed
        if not appeal:
            transaction.appealed = appeal
            self.session.commit()
        elif transaction.status in (
            TransactionStatus.ACCEPTED,
            TransactionStatus.UNDETERMINED,
        ):
            transaction.appealed = appeal
            self.set_transaction_timestamp_appeal(transaction, int(time.time()))
            self.session.commit()

    def set_transaction_timestamp_awaiting_finalization(
        self, transaction_hash: str, timestamp_awaiting_finalization: int = None
    ):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        if timestamp_awaiting_finalization:
            transaction.timestamp_awaiting_finalization = (
                timestamp_awaiting_finalization
            )
        else:
            transaction.timestamp_awaiting_finalization = int(time.time())

    def set_transaction_appeal_failed(self, transaction_hash: str, appeal_failed: int):
        if appeal_failed < 0:
            raise ValueError("appeal_failed must be a non-negative integer")
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transaction.appeal_failed = appeal_failed

    def set_transaction_appeal_undetermined(
        self, transaction_hash: str, appeal_undetermined: bool
    ):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transaction.appeal_undetermined = appeal_undetermined

    def get_highest_timestamp(self) -> int:
        transaction = (
            self.session.query(Transactions)
            .filter(Transactions.timestamp_awaiting_finalization.isnot(None))
            .order_by(desc(Transactions.timestamp_awaiting_finalization))
            .first()
        )
        if transaction is None:
            return 0
        return transaction.timestamp_awaiting_finalization

    def get_transactions_for_block(
        self, block_number: int, include_full_tx: bool
    ) -> dict:
        transactions = (
            self.session.query(Transactions)
            .filter(Transactions.timestamp_awaiting_finalization == block_number)
            .all()
        )

        block_hash = "0x" + "0" * 64
        parent_hash = "0x" + "0" * 64  # Placeholder for parent block hash
        timestamp = (
            transactions[0].timestamp_awaiting_finalization
            if len(transactions) > 0
            else int(time.time())
        )

        if include_full_tx:
            transaction_data = [self._parse_transaction_data(tx) for tx in transactions]
        else:
            transaction_data = [tx.hash for tx in transactions]

        block_details = {
            "number": hex(block_number),
            "hash": block_hash,
            "parentHash": parent_hash,
            "nonce": "0x" + "0" * 16,
            "transactions": transaction_data,
            "timestamp": hex(int(timestamp)),
            "miner": "0x" + "0" * 40,
            "difficulty": "0x1",
            "gasUsed": "0x0",
            "gasLimit": "0x0",
            "size": "0x0",
            "extraData": "0x",
        }

        return block_details

    def get_newer_transactions(self, transaction_hash: str):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transactions = (
            self.session.query(Transactions)
            .filter(
                Transactions.created_at > transaction.created_at,
                Transactions.to_address == transaction.to_address,
            )
            .order_by(Transactions.created_at)
            .all()
        )
        return [
            self._parse_transaction_data(transaction) for transaction in transactions
        ]

    def update_consensus_history(
        self,
        transaction_hash: str,
        consensus_round: str,
        leader_result: list[Receipt] | None,
        validator_results: list[Receipt],
        extra_status_change: TransactionStatus | None = None,
    ):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )

        status_changes_to_use = (
            transaction.consensus_history["current_status_changes"]
            if "current_status_changes" in transaction.consensus_history
            else []
        )
        if extra_status_change:
            status_changes_to_use.append(extra_status_change.value)

        current_consensus_results = {
            "consensus_round": consensus_round,
            "leader_result": (
                [receipt.to_dict() for receipt in leader_result]
                if leader_result
                else None
            ),
            "validator_results": [receipt.to_dict() for receipt in validator_results],
            "status_changes": status_changes_to_use,
        }

        if "consensus_results" in transaction.consensus_history:
            transaction.consensus_history["consensus_results"].append(
                current_consensus_results
            )
        else:
            transaction.consensus_history["consensus_results"] = [
                current_consensus_results
            ]

        transaction.consensus_history["current_status_changes"] = []

        flag_modified(transaction, "consensus_history")
        self.session.commit()

    def reset_consensus_history(self, transaction_hash: str):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transaction.consensus_history = {}
        self.session.commit()

    def set_transaction_timestamp_appeal(
        self, transaction: Transactions | str, timestamp_appeal: int | None
    ):
        if isinstance(transaction, str):  # hash
            transaction = (
                self.session.query(Transactions).filter_by(hash=transaction).one()
            )
        transaction.timestamp_appeal = timestamp_appeal

    def set_transaction_appeal_processing_time(self, transaction_hash: str):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transaction.appeal_processing_time += (
            round(time.time()) - transaction.timestamp_appeal
        )
        flag_modified(transaction, "appeal_processing_time")
        self.session.commit()

    def reset_transaction_appeal_processing_time(self, transaction_hash: str):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transaction.appeal_processing_time = 0
        self.session.commit()

    def set_transaction_contract_snapshot(
        self, transaction_hash: str, contract_snapshot: dict | None
    ):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transaction.contract_snapshot = contract_snapshot
        self.session.commit()

    def transactions_in_process_by_contract(self) -> list[dict]:
        transactions = (
            self.session.query(Transactions)
            .filter(
                Transactions.to_address.isnot(None),
                Transactions.status.in_(
                    [
                        TransactionStatus.ACTIVATED,
                        TransactionStatus.PROPOSING,
                        TransactionStatus.COMMITTING,
                        TransactionStatus.REVEALING,
                    ]
                ),
            )
            .distinct(Transactions.to_address)
            .order_by(Transactions.to_address, Transactions.created_at.asc())
            .all()
        )

        return [
            self._parse_transaction_data(transaction) for transaction in transactions
        ]

    def get_previous_transaction(
        self,
        transaction_hash: str,
        status: TransactionStatus | None = None,
        filter_success: bool = False,
    ) -> dict | None:
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )

        filters = [
            Transactions.created_at < transaction.created_at,
            Transactions.to_address == transaction.to_address,
        ]
        if status is not None:
            filters.append(Transactions.status == status)

        if filter_success:
            consensus_data = type_coerce(Transactions.consensus_data, JSON)

            # Handle both formats of leader_receipt (dict and array)
            filters.append(
                and_(
                    consensus_data.isnot(None),
                    consensus_data["leader_receipt"].isnot(None),
                    text(
                        """
                        (
                            (jsonb_typeof(consensus_data::jsonb->'leader_receipt') = 'object'
                             AND consensus_data::jsonb->'leader_receipt'->>'execution_result' = :status)
                            OR
                            (jsonb_typeof(consensus_data::jsonb->'leader_receipt') = 'array'
                             AND consensus_data::jsonb->'leader_receipt'->0->>'execution_result' = :status)
                        )
                    """
                    ).bindparams(status=ExecutionResultStatus.SUCCESS.value),
                )
            )

        closest_transaction = (
            self.session.query(Transactions)
            .filter(*filters)
            .order_by(desc(Transactions.created_at))
            .first()
        )

        return (
            self._parse_transaction_data(closest_transaction)
            if closest_transaction
            else None
        )
