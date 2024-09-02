from sqlalchemy.orm import Session

from backend.database_handler.chain_snapshot import ChainSnapshot
from backend.database_handler.models import Transactions
from backend.database_handler.transactions_processor import TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor


def test_chain_snapshot(session: Session):
    pending_transaction_1 = Transactions(
        status=TransactionStatus.PENDING,
        from_address="0x123",
        to_address="0x456",
        data="data",
        consensus_data="consensus_data",
        value=10,
        type=0,
        gaslimit=None,
        input_data=None,
        nonce=None,
        r=None,
        s=None,
        v=None,
    )

    pending_transaction_2 = Transactions(
        status=TransactionStatus.PENDING,
        from_address="0x789",
        to_address="0xabc",
        data="data",
        consensus_data="consensus_data",
        value=20,
        type=0,
        gaslimit=None,
        input_data=None,
        nonce=None,
        r=None,
        s=None,
        v=None,
    )

    finalized_transaction = Transactions(
        status=TransactionStatus.FINALIZED,
        from_address="0xdef",
        to_address="0x123",
        data="data",
        consensus_data="consensus_data",
        value=30,
        type=0,
        gaslimit=None,
        input_data=None,
        nonce=None,
        r=None,
        s=None,
        v=None,
    )

    session.add(pending_transaction_1)
    session.add(pending_transaction_2)
    session.add(finalized_transaction)
    session.commit()

    chain_snapshot = ChainSnapshot(session)
    pending_transactions = chain_snapshot.get_pending_transactions()

    assert len(pending_transactions) == 2
    pending_transactions.sort(key=lambda x: x["id"])

    assert (
        TransactionsProcessor._parse_transaction_data(pending_transaction_1)
        == pending_transactions[0]
    )

    assert (
        TransactionsProcessor._parse_transaction_data(pending_transaction_2)
        == pending_transactions[1]
    )
