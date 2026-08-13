"""Failing repro: FinalizingState promotes the LIVE accepted bucket into
finalized, not the state the finalizing transaction produced.

Timeline:
  T1: RUN_CONTRACT -> ACCEPTED, its post-state was S1 (code + slot_a)
  T2: RUN_CONTRACT -> ACCEPTED (still inside its own appeal window), post-state
      S2 = S1 + slot_b  => the live DB accepted bucket is now S2
  T1 passes its finality window -> FinalizingState runs for T1.

FinalizingState.handle sources the finalized state from
context.contract_snapshot_factory(to_address).states["accepted"] -- the CURRENT
accepted bucket (S2) -- so T2's not-yet-finalized, still-appealable slot_b is
copied into `finalized`. A later gen_call/eth_call with status="finalized"
then serves state that was never finalized (and, if T2's appeal succeeds,
never will be). No fix here -- failing reproduction only.
"""

import asyncio
import base64

from sqlalchemy.orm import sessionmaker

from backend.consensus.base import FinalizingState
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.models import CurrentState, TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.types import ConsensusData
from backend.domain.types import Transaction, TransactionType
from backend.node.types import Receipt, ExecutionMode, ExecutionResultStatus
from eth_utils import to_checksum_address


CODE_SLOT = base64.b64encode(b"\x00" * 32).decode()
SLOT_A = base64.b64encode(b"\x01" * 32).decode()
SLOT_B = base64.b64encode(b"\x02" * 32).decode()

S0 = {CODE_SLOT: "Y29kZQ=="}
S1 = {**S0, SLOT_A: "dDE="}
S2 = {**S1, SLOT_B: "dDI="}

SENDER = to_checksum_address("0xaa00000000000000000000000000000000000001")
CONTRACT = to_checksum_address("0xbb00000000000000000000000000000000000001")


class _StubMsgHandler:
    def send_message(self, *a, **k):
        pass


class _StubConsensusService:
    def emit_transaction_event(self, *a, **k):
        return None


def _stripped_leader_receipt():
    # consensus_data is persisted with strip_contract_state=True.
    return Receipt(
        result=b"\x00\x00",
        calldata=b"\x06",
        gas_used=0,
        mode=ExecutionMode.LEADER,
        contract_state={},
        node_config={"address": "0xLeader", "stake": 100},
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=None,
        eq_outputs={},
        pending_transactions=[],
        genvm_result={"stdout": "", "stderr": ""},
    )


def test_finalization_does_not_promote_younger_txs_accepted_state(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        am = AccountsManager(session)
        am.create_new_account_with_address(SENDER)
        am.create_new_account_with_address(CONTRACT)

        tp = TransactionsProcessor(session)
        t1_hash = "0x" + "11" * 32
        tp.insert_transaction(
            from_address=SENDER,
            to_address=CONTRACT,
            data={"calldata": base64.b64encode(b"\x06").decode()},
            value=0,
            type=TransactionType.RUN_CONTRACT.value,
            nonce=0,
            leader_only=False,
            config_rotation_rounds=3,
            transaction_hash=t1_hash,
        )
        tp.update_transaction_status(t1_hash, TransactionStatus.ACCEPTED)
        session.commit()

        # Live accepted bucket = S2 (T1 and T2 both accepted); finalized = S0.
        row = session.query(CurrentState).filter_by(id=CONTRACT).one()
        row.data = {"state": {"accepted": dict(S2), "finalized": dict(S0)}}
        session.commit()

        t1 = Transaction(
            hash=t1_hash,
            status=TransactionStatus.ACCEPTED,
            type=TransactionType.RUN_CONTRACT,
            from_address=SENDER,
            to_address=CONTRACT,
            data={"calldata": base64.b64encode(b"\x06").decode()},
            consensus_data=ConsensusData(
                votes={}, leader_receipt=[_stripped_leader_receipt()], validators=[]
            ),
        )

        context = type("Ctx", (), {})()
        context.transaction = t1
        context.transactions_processor = tp
        context.contract_processor = ContractProcessor(session)
        context.msg_handler = _StubMsgHandler()
        context.consensus_service = _StubConsensusService()
        context.contract_snapshot_factory = lambda addr: ContractSnapshot(addr, session)
        context.accounts_manager = am

        asyncio.run(FinalizingState().handle(context))
        session.commit()

        session.expire_all()
        finalized = (
            session.query(CurrentState)
            .filter_by(id=CONTRACT)
            .one()
            .data["state"]["finalized"]
        )
        assert SLOT_B not in finalized, (
            "finalizing T1 promoted a younger, still-appealable transaction's "
            "accepted state (slot_b) into the finalized bucket"
        )
    finally:
        session.close()
