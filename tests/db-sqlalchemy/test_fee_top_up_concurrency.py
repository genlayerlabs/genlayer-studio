"""PostgreSQL regression coverage for serial fee top-up accounting."""

import json
import threading
from copy import deepcopy

from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc.fees import StudioFeePolicy, create_fee_accounting


def _fees_distribution(*, leader=100, validator=200, rotations=None):
    return {
        "leaderTimeunitsAllocation": leader,
        "validatorTimeunitsAllocation": validator,
        "appealRounds": 0,
        "executionBudgetPerRound": 0,
        "executionConsumed": 0,
        "totalMessageFees": 0,
        "rotations": [0] if rotations is None else rotations,
        "maxPriceGenPerTimeUnit": 0,
        "storageFeeMaxGasPrice": 0,
        "receiptFeeMaxGasPrice": 0,
    }


def test_concurrent_fee_top_ups_compose_without_lost_accounting(engine: Engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    tx_hash = "0x" + "ab" * 32
    sender = "0x1111111111111111111111111111111111111111"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
        sender=sender,
        policy=StudioFeePolicy(),
    )
    with Session() as session:
        session.execute(
            text(
                """
                INSERT INTO transactions (
                    hash, status, from_address, to_address, data, value, type,
                    nonce, leader_only, execution_mode, appealed, appeal_failed,
                    appeal_undetermined, appeal_leader_timeout,
                    appeal_validators_timeout, appeal_processing_time,
                    value_credited, consensus_history,
                    num_of_initial_validators
                ) VALUES (
                    :hash, 'PENDING', :sender, '0xcontract', CAST(:data AS jsonb),
                    0, 2, 0, false, 'NORMAL', false, 0, false, false, false, 0,
                    false, CAST('{}' AS jsonb), 5
                )
                """
            ),
            {
                "hash": tx_hash,
                "sender": sender,
                "data": json.dumps({"fee_accounting": accounting}),
            },
        )
        session.commit()

    barrier = threading.Barrier(2, timeout=5)
    errors: list[BaseException] = []

    def top_up(amount: int):
        try:
            with Session() as session:
                processor = TransactionsProcessor(session)
                barrier.wait()
                processor.apply_transaction_fee_top_up(
                    tx_hash,
                    fees_distribution=_fees_distribution(
                        leader=0, validator=0, rotations=[]
                    ),
                    amount=amount,
                    sender=sender,
                    policy=StudioFeePolicy(),
                )
                session.commit()
        except BaseException as exc:  # surfaced in the parent test thread
            errors.append(exc)

    first = threading.Thread(target=top_up, args=(7,))
    second = threading.Thread(target=top_up, args=(11,))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    with Session() as session:
        data = session.execute(
            text("SELECT data FROM transactions WHERE hash = :hash"),
            {"hash": tx_hash},
        ).scalar_one()

    updated = data["fee_accounting"]
    assert updated["paid_fee_value"] == 1_118
    assert updated["primary_fee_budget"] == 1_118
    assert sorted(item["amount"] for item in updated["top_ups"]) == [7, 11, 1_100]

    # A worker mutation and a later permitted RPC top-up must also compose.
    # Previously the worker could write its pre-top-up JSON snapshot after the
    # payer had already been charged, silently erasing that contribution.
    barrier = threading.Barrier(2, timeout=5)
    errors.clear()

    def worker_mutation():
        try:
            with Session() as session:
                processor = TransactionsProcessor(session)
                barrier.wait()

                def mark_execution(current):
                    changed = deepcopy(current)
                    changed["concurrency_test_execution_marker"] = 23
                    return changed

                processor.mutate_transaction_fee_accounting(
                    tx_hash,
                    mark_execution,
                )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=worker_mutation)
    payer = threading.Thread(target=top_up, args=(13,))
    worker.start()
    payer.start()
    worker.join(timeout=10)
    payer.join(timeout=10)

    assert not worker.is_alive() and not payer.is_alive()
    assert errors == []
    with Session() as session:
        data = session.execute(
            text("SELECT data FROM transactions WHERE hash = :hash"),
            {"hash": tx_hash},
        ).scalar_one()

    updated = data["fee_accounting"]
    assert updated["paid_fee_value"] == 1_131
    assert updated["primary_fee_budget"] == 1_131
    assert updated["concurrency_test_execution_marker"] == 23
    assert sorted(item["amount"] for item in updated["top_ups"]) == [
        7,
        11,
        13,
        1_100,
    ]
