"""Failing repro: claim_next_appeal drops the saved pre-execution contract
snapshot.

At first acceptance, AcceptedState saves the transaction's PRE-execution
contract snapshot on transactions.contract_snapshot. When the tx is later
appealed, ConsensusWorker.claim_next_appeal's RETURNING clause does not select
that column, so the claimed dict has no `contract_snapshot` key and
Transaction.from_dict(...).contract_snapshot is None. Downstream this is severe:

  * TransactionContext falls back to the LIVE post-tx accepted state, so appeal
    validators re-execute the appealed tx on top of its own output -> a
    guaranteed contract_state_hash mismatch vs the leader -> a false
    DETERMINISTIC_VIOLATION -> a correct transaction's appeal spuriously
    succeeds.
  * On VALIDATOR_APPEAL_SUCCESSFUL the restore defaults previous state to {}
    and writes update_contract_state(accepted_state={}), wiping the contract's
    accepted bucket (code slot included) -> the contract is bricked.

This test pins the root cause. No fix -- failing reproduction only.
"""

import asyncio
import base64
import types

from sqlalchemy.orm import sessionmaker

from backend.consensus.worker import ConsensusWorker
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.models import CurrentState, TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.types import ConsensusData
from backend.domain.types import Transaction, TransactionType
from backend.node.types import Receipt, ExecutionMode, ExecutionResultStatus
from eth_utils import to_checksum_address


CODE_SLOT = base64.b64encode(b"\x00" * 32).decode()
SLOT_A = base64.b64encode(b"\x01" * 32).decode()
PRE_STATE = {CODE_SLOT: "Y29kZQ=="}
POST_STATE = {CODE_SLOT: "Y29kZQ==", SLOT_A: "dDE="}

SENDER = to_checksum_address("0xaa00000000000000000000000000000000000003")
CONTRACT = to_checksum_address("0xbb00000000000000000000000000000000000003")


def _leader_receipt():
    return Receipt(
        result=b"\x00\x00",
        calldata=b"\x06",
        gas_used=0,
        mode=ExecutionMode.LEADER,
        contract_state=dict(POST_STATE),
        node_config={"address": "0xLeader", "stake": 100},
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=None,
        eq_outputs={},
        pending_transactions=[],
        genvm_result={"stdout": "", "stderr": ""},
    )


def test_claim_next_appeal_preserves_contract_snapshot(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        am = AccountsManager(session)
        am.create_new_account_with_address(SENDER)
        am.create_new_account_with_address(CONTRACT)

        tp = TransactionsProcessor(session)
        tx_hash = "0x" + "33" * 32
        tp.insert_transaction(
            from_address=SENDER,
            to_address=CONTRACT,
            data={"calldata": base64.b64encode(b"\x06").decode()},
            value=0,
            type=TransactionType.RUN_CONTRACT.value,
            nonce=0,
            leader_only=False,
            config_rotation_rounds=3,
            transaction_hash=tx_hash,
        )
        cd = ConsensusData(votes={}, leader_receipt=[_leader_receipt()], validators=[])
        tp.set_transaction_result(tx_hash, cd.to_dict(strip_contract_state=True))
        # AcceptedState saved the PRE-execution snapshot here.
        tp.set_transaction_contract_snapshot(
            tx_hash,
            {
                "contract_address": CONTRACT,
                "states": {"accepted": dict(PRE_STATE), "finalized": {}},
                "balance": 0,
            },
        )
        tp.update_transaction_status(tx_hash, TransactionStatus.ACCEPTED)
        row = session.query(CurrentState).filter_by(id=CONTRACT).one()
        row.data = {"state": {"accepted": dict(POST_STATE), "finalized": dict(PRE_STATE)}}
        tp.set_transaction_appeal(tx_hash, True)
        session.commit()

        # Sanity: the snapshot really is stored in the DB.
        assert tp.get_transaction_by_hash(tx_hash)["contract_snapshot"]

        stub = types.SimpleNamespace(
            worker_id="repro-worker",
            transaction_timeout_minutes=5,
            _log_query_result=lambda *a, **k: None,
        )
        appeal_data = asyncio.run(ConsensusWorker.claim_next_appeal(stub, session))
        assert appeal_data is not None, "the appealed tx should be claimable"

        transaction = Transaction.from_dict(appeal_data)
        assert transaction.contract_snapshot is not None, (
            "claim_next_appeal dropped the saved pre-execution contract "
            "snapshot; appeal validators would execute from the live post-tx "
            "state and a successful appeal would wipe the accepted bucket"
        )
    finally:
        session.close()
