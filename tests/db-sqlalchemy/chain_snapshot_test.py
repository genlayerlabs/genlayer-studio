from sqlalchemy.orm import Session

from backend.database_handler.chain_snapshot import ChainSnapshot
from backend.database_handler.models import Transactions
from backend.database_handler.transactions_processor import TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor


def test_chain_snapshot(session: Session):
    pending_transaction_1 = Transactions(
        status=TransactionStatus.PENDING,
        hash="0x123",
        from_address="0x123",
        to_address="0x456",
        data={},
        consensus_data={},
        value=10,
        type=0,
        gaslimit=0,
        input_data={},
        nonce=0,
        r=0,
        s=0,
        v=0,
        leader_only=False,
        appeal_failed=0,
        consensus_history={},
        timestamp_appeal=0,
        appeal_processing_time=0,
        contract_snapshot={},
        config_rotation_rounds=0,
        appealed=False,
        appeal_undetermined=False,
        timestamp_awaiting_finalization=0,
        num_of_initial_validators=5,
        last_vote_timestamp=0,
        rotation_count=0,
        appeal_leader_timeout=False,
        leader_timeout_validators=None,
        appeal_validators_timeout=False,
        # triggered_by_hash=None,
    )

    pending_transaction_2 = Transactions(
        status=TransactionStatus.PENDING,
        from_address="0x789",
        to_address="0xabc",
        hash="0x456",
        data={},
        consensus_data={},
        value=20,
        type=0,
        gaslimit=0,
        input_data={},
        nonce=0,
        r=0,
        s=0,
        v=0,
        leader_only=False,
        appeal_failed=0,
        consensus_history={},
        timestamp_appeal=0,
        appeal_processing_time=0,
        contract_snapshot={},
        config_rotation_rounds=0,
        appealed=False,
        appeal_undetermined=False,
        timestamp_awaiting_finalization=0,
        num_of_initial_validators=5,
        last_vote_timestamp=0,
        rotation_count=0,
        appeal_leader_timeout=False,
        leader_timeout_validators=None,
        appeal_validators_timeout=False,
        # triggered_by_hash="0xdef",
    )

    finalized_transaction = Transactions(
        status=TransactionStatus.FINALIZED,
        hash="0xdef",
        from_address="0xdef",
        to_address="0x123",
        data={},
        consensus_data={},
        value=30,
        type=0,
        gaslimit=0,
        input_data={},
        nonce=0,
        r=0,
        s=0,
        v=0,
        leader_only=False,
        appeal_failed=0,
        consensus_history={},
        timestamp_appeal=0,
        appeal_processing_time=0,
        contract_snapshot={},
        config_rotation_rounds=0,
        appealed=False,
        appeal_undetermined=False,
        timestamp_awaiting_finalization=0,
        num_of_initial_validators=5,
        last_vote_timestamp=0,
        rotation_count=0,
        appeal_leader_timeout=False,
        leader_timeout_validators=None,
        appeal_validators_timeout=False,
        # triggered_by_hash=None,
    )

    session.add(pending_transaction_1)
    session.add(pending_transaction_2)
    session.add(finalized_transaction)
    session.commit()

    chain_snapshot = ChainSnapshot(session)
    pending_transactions = chain_snapshot.get_pending_transactions()

    assert len(pending_transactions) == 2
    pending_transactions.sort(key=lambda x: x["hash"])

    assert (
        TransactionsProcessor._parse_transaction_data(pending_transaction_1)
        == pending_transactions[0]
    )

    assert (
        TransactionsProcessor._parse_transaction_data(pending_transaction_2)
        == pending_transactions[1]
    )
