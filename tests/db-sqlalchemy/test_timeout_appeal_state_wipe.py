"""Failing repro: a successful VALIDATORS_TIMEOUT appeal overwrites the
contract's accepted state with the STRIPPED {} contract_state from the persisted
leader receipt -> the contract (code slot included) is wiped.

Real path:
  1. Tx reaches VALIDATORS_TIMEOUT; consensus_data is persisted with
     strip_contract_state=True, so the DB leader receipt has contract_state {}
     (the contract_state_hash is kept).
  2. User appeals; the worker claims it (raw consensus_data column) and
     Transaction.from_dict yields a leader receipt with contract_state == {}.
  3. process_validator_appeal, VALIDATORS_TIMEOUT branch: clears `appealed`,
     carries the stripped leader receipt straight into CommittingState (the
     leader receipt is never regenerated -- no ProposingState).
  4. Appeal validators AGREE via the preserved contract_state_hash ->
     RevealingState -> AcceptedState.
  5. AcceptedState.handle: accepted_contract_state = leader_receipt
     .contract_state == {}; with appealed == False, decide_accepted emits
     UpdateContractStateEffect(accepted_state={}) -> the accepted bucket is
     overwritten with {}.

No fix -- failing reproduction only (verified end-to-end against Postgres).
"""

import asyncio
import base64
import types

from sqlalchemy.orm import sessionmaker

from backend.consensus.base import AcceptedState
from backend.consensus.worker import ConsensusWorker
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.models import CurrentState, TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.types import ConsensusData
from backend.domain.types import Transaction, TransactionType
from backend.node.types import Receipt, ExecutionMode, ExecutionResultStatus, Vote
from eth_utils import to_checksum_address


CODE_SLOT = base64.b64encode(b"\x00" * 32).decode()
SLOT_A = base64.b64encode(b"\x01" * 32).decode()
REAL_STATE = {CODE_SLOT: "Y29kZQ==", SLOT_A: "dDE="}

SENDER = to_checksum_address("0xaa00000000000000000000000000000000000002")
CONTRACT = to_checksum_address("0xbb00000000000000000000000000000000000002")


class _StubMsgHandler:
    def send_message(self, *a, **k):
        pass


class _StubConsensusService:
    def emit_transaction_event(self, *a, **k):
        return None


def test_validators_timeout_appeal_does_not_wipe_accepted_state(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        am = AccountsManager(session)
        am.create_new_account_with_address(SENDER)
        am.create_new_account_with_address(CONTRACT)

        tp = TransactionsProcessor(session)
        tx_hash = "0x" + "22" * 32
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
        session.commit()

        row = session.query(CurrentState).filter_by(id=CONTRACT).one()
        row.data = {
            "state": {
                "accepted": dict(REAL_STATE),
                "finalized": {CODE_SLOT: "Y29kZQ=="},
            }
        }
        session.commit()

        leader_receipt = Receipt(
            result=b"\x00\x00",
            calldata=b"\x06",
            gas_used=0,
            mode=ExecutionMode.LEADER,
            contract_state=dict(REAL_STATE),
            node_config={"address": "0xLeader", "stake": 100},
            execution_result=ExecutionResultStatus.SUCCESS,
            vote=None,
            eq_outputs={},
            pending_transactions=[],
            genvm_result={"stdout": "", "stderr": ""},
        )
        cd = ConsensusData(votes={}, leader_receipt=[leader_receipt], validators=[])
        tp.set_transaction_result(tx_hash, cd.to_dict(strip_contract_state=True))
        tp.update_transaction_status(tx_hash, TransactionStatus.VALIDATORS_TIMEOUT)
        tp.set_transaction_appeal(tx_hash, True)
        session.commit()

        stub = types.SimpleNamespace(
            worker_id="repro-worker",
            transaction_timeout_minutes=5,
            _log_query_result=lambda *a, **k: None,
        )
        appeal_data = asyncio.run(ConsensusWorker.claim_next_appeal(stub, session))
        assert appeal_data is not None
        transaction = Transaction.from_dict(appeal_data)
        # VALIDATORS_TIMEOUT appeal branch clears appealed and carries the
        # stripped leader receipt straight into re-acceptance.
        transaction.appealed = False

        context = type("Ctx", (), {})()
        context.transaction = transaction
        context.transactions_processor = tp
        context.contract_processor = ContractProcessor(session)
        context.accounts_manager = am
        context.msg_handler = _StubMsgHandler()
        context.consensus_service = _StubConsensusService()
        context.contract_snapshot_factory = lambda addr: ContractSnapshot(addr, session)
        context.contract_snapshot = ContractSnapshot(CONTRACT, session)
        context.consensus_data = ConsensusData(
            votes={"0xV1": Vote.AGREE.value},
            leader_receipt=transaction.consensus_data.leader_receipt,
            validators=[],
        )
        context.validation_results = []

        asyncio.run(AcceptedState().handle(context))
        session.commit()

        session.expire_all()
        accepted = (
            session.query(CurrentState)
            .filter_by(id=CONTRACT)
            .one()
            .data["state"]["accepted"]
        )
        assert accepted != {}, (
            "re-acceptance from the stripped leader receipt wiped the contract's "
            "accepted state (code slot included) to {}"
        )
    finally:
        session.close()
