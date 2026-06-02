"""Comprehensive consensus-level tests for payable value scenarios.

All tests use real PostgreSQL but mock GenVM execution.
"""

import base64
import secrets
from unittest.mock import Mock, AsyncMock, MagicMock

import pytest
from eth_abi import encode

from backend.consensus.base import ConsensusAlgorithm
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import Transaction, TransactionType
from backend.node.types import (
    Receipt,
    PendingTransaction,
    ExecutionMode,
    ExecutionResultStatus,
    Vote,
)
from backend.protocol_rpc.fees import (
    FEE_ACCOUNTING_KEY,
    NODE_ROOT_SENTINEL,
    StudioFeePolicy,
    create_fee_accounting,
    decode_internal_message_fee_params,
    min_message_primary_fees,
    required_fee_deposit,
)
import backend.validators as validators

WEI_PER_GEN = 10**18


# ── Helpers ──


def _receipt(
    pending_transactions=None,
    contract_state=None,
    execution_result=ExecutionResultStatus.SUCCESS,
):
    return Receipt(
        vote=Vote.AGREE,
        execution_result=execution_result,
        result=b"\x01",
        calldata=b"\x06",
        gas_used=100,
        mode=ExecutionMode.LEADER,
        contract_state=contract_state or {"slot": "data"},
        node_config={
            "address": "0x5000000000000000000000000000000000000001",
            "provider": "test",
            "model": "test",
            "config": {},
        },
        eq_outputs=None,
        pending_transactions=pending_transactions or [],
    )


def _pending_tx(
    value=0,
    on="accepted",
    address="0xChildTarget",
    is_eth_send=False,
    fee_params=b"",
    declared_budget=0,
    call_key="0x" + ("0" * 64),
    allocation_subtree=None,
    gas_used=0,
):
    return PendingTransaction(
        address=address,
        calldata=b"\x06",
        code=None,
        salt_nonce=0,
        on=on,
        value=value,
        is_eth_send=is_eth_send,
        fee_params=fee_params,
        declared_budget=declared_budget,
        call_key=call_key,
        allocation_subtree=allocation_subtree or [],
        gas_used=gas_used,
    )


def _node_factory(receipt):
    observed_balances = []

    def factory(*args, **kwargs):
        if len(args) > 2 and hasattr(args[2], "balance"):
            observed_balances.append(args[2].balance)
        node = MagicMock()
        node.exec_transaction = AsyncMock(return_value=receipt)
        return node

    factory.observed_balances = observed_balances
    return factory


def _validators_snapshot():
    validator = Mock()
    validator.address = "0x5000000000000000000000000000000000000001"
    validator.to_dict.return_value = {
        "address": "0x5000000000000000000000000000000000000001",
        "stake": 100,
        "provider": "test",
        "model": "test",
        "config": {},
        "plugin": "test",
        "plugin_config": {},
    }
    node = Mock()
    node.validator = validator
    node.genvm_host_data = {}
    snapshot = Mock(spec=validators.Snapshot)
    snapshot.nodes = [node]
    return snapshot


def _consensus(session):
    msg_handler = MagicMock()
    msg_handler.send_message_async = AsyncMock()
    return ConsensusAlgorithm(
        get_session=lambda: session,
        msg_handler=msg_handler,
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
    )


def _insert_tx(
    tp,
    session,
    from_addr,
    to_addr,
    value=0,
    tx_type=TransactionType.RUN_CONTRACT,
    data=None,
):
    tx_hash = "0x" + secrets.token_hex(32)
    tp.insert_transaction(
        from_address=from_addr,
        to_address=to_addr,
        data=data or {"calldata": base64.b64encode(b"\x06").decode("ascii")},
        value=value,
        type=tx_type.value,
        nonce=0,
        leader_only=False,
        config_rotation_rounds=3,
        num_of_initial_validators=5,
        transaction_hash=tx_hash,
    )
    session.commit()
    return tx_hash


def _fees_distribution(total_message_fees=0, appeal_rounds=0, rotations=None):
    if rotations is None:
        rotations = [0] * (appeal_rounds + 1)
    return {
        "leaderTimeunitsAllocation": 100,
        "validatorTimeunitsAllocation": 200,
        "appealRounds": appeal_rounds,
        "executionBudgetPerRound": 0,
        "executionConsumed": 0,
        "totalMessageFees": total_message_fees,
        "rotations": rotations,
        "maxPriceGenPerTimeUnit": 0,
        "storageFeeMaxGasPrice": 0,
        "receiptFeeMaxGasPrice": 0,
    }


def _encode_internal_fee_params(
    *,
    leader_timeunits=5,
    validator_timeunits=10,
    appeals=0,
    execution_budget_per_round=0,
    rotations=None,
):
    if rotations is None:
        rotations = [0] * (appeals + 1)
    return encode(
        ["(uint256,uint256,uint256,uint256,uint256[])"],
        [
            (
                leader_timeunits,
                validator_timeunits,
                appeals,
                execution_budget_per_round,
                rotations,
            )
        ],
    )


def _encode_external_fee_params(*, gas_limit=100, max_gas_price=10):
    return encode(
        ["(uint256,uint256)"],
        [
            (
                gas_limit,
                max_gas_price,
            )
        ],
    )


def _allocation(
    *,
    recipient,
    call_key="0x" + ("0" * 64),
    budget=70,
    fee_params=None,
):
    return {
        "messageType": 1,
        "onAcceptance": True,
        "parentIndex": NODE_ROOT_SENTINEL,
        "recipient": recipient,
        "callKey": call_key,
        "budget": budget,
        "feeParams": fee_params or _encode_internal_fee_params(),
    }


def _external_allocation(
    *,
    recipient,
    call_key="0x" + ("0" * 64),
    budget=1_000,
    fee_params=None,
):
    return {
        "messageType": 0,
        "onAcceptance": False,
        "parentIndex": NODE_ROOT_SENTINEL,
        "recipient": recipient,
        "callKey": call_key,
        "budget": budget,
        "feeParams": fee_params or _encode_external_fee_params(),
    }


def _message_primary_fee(fee_params, policy):
    return min_message_primary_fees(
        decode_internal_message_fee_params(fee_params),
        policy,
    )


def _create_fee_accounting(sender, fees_distribution, *, message_allocations=None):
    policy = StudioFeePolicy.from_env()
    return create_fee_accounting(
        fees_distribution=fees_distribution,
        message_allocations=message_allocations or [],
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        sender=sender,
        policy=policy,
    )


def _amount(value):
    return int(value)


def _amount_map(values):
    return {key: int(value) for key, value in values.items()}


def _setup_contract(session, accounts_manager, contract_addr, balance):
    from backend.database_handler.models import CurrentState

    row = session.query(CurrentState).filter_by(id=contract_addr).one()
    row.data = {
        "state": {
            "accepted": {"slot": base64.b64encode(b"data").decode()},
            "finalized": {},
        }
    }
    session.commit()
    accounts_manager.update_account_balance(contract_addr, balance)
    session.commit()


async def _run_tx(
    session,
    accounts_manager,
    transactions_processor,
    contract_processor,
    tx_hash,
    receipt,
):
    consensus = _consensus(session)
    tx_data = transactions_processor.get_transaction_by_hash(tx_hash)
    transaction = Transaction.from_dict(tx_data)
    nf = _node_factory(receipt)
    await consensus.exec_transaction(
        transaction=transaction,
        transactions_processor=transactions_processor,
        chain_snapshot=None,
        accounts_manager=accounts_manager,
        contract_snapshot_factory=lambda addr: ContractSnapshot(addr, session),
        contract_processor=contract_processor,
        node_factory=nf,
        validators_snapshot=_validators_snapshot(),
    )
    session.expire_all()
    return nf


async def _finalize_tx(
    session,
    accounts_manager,
    transactions_processor,
    contract_processor,
    tx_hash,
):
    consensus = _consensus(session)
    tx_data = transactions_processor.get_transaction_by_hash(tx_hash)
    transaction = Transaction.from_dict(tx_data)
    await consensus.process_finalization(
        transaction=transaction,
        transactions_processor=transactions_processor,
        chain_snapshot=None,
        accounts_manager=accounts_manager,
        contract_snapshot_factory=lambda addr: ContractSnapshot(addr, session),
        contract_processor=contract_processor,
        node_factory=MagicMock(),
    )
    session.expire_all()


# ── Fixtures ──


@pytest.fixture
def addrs(accounts_manager, session):
    sender = "0x1000000000000000000000000000000000000001"
    contract = "0x2000000000000000000000000000000000000001"
    child1 = "0x3000000000000000000000000000000000000001"
    child2 = "0x4000000000000000000000000000000000000001"
    for addr in [sender, contract, child1, child2]:
        accounts_manager.create_new_account_with_address(addr)
    session.commit()
    return sender, contract, child1, child2


# ── Tests ──


class TestMultipleMessages:
    """Two on_accepted messages: both debited, both children created with unique nonces."""

    @pytest.mark.asyncio
    async def test_two_accepted_messages(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, child2 = addrs
        _setup_contract(session, accounts_manager, contract, 20 * WEI_PER_GEN)

        tx_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(value=3 * WEI_PER_GEN, on="accepted", address=child1),
                _pending_tx(value=5 * WEI_PER_GEN, on="accepted", address=child2),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        assert (
            accounts_manager.get_account_balance(contract) == 12 * WEI_PER_GEN
        )  # 20 - 3 - 5

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT value, nonce FROM transactions WHERE triggered_by_hash = :h ORDER BY nonce"
            ),
            {"h": tx_hash},
        ).fetchall()
        assert len(children) == 2
        assert children[0].value == 3 * WEI_PER_GEN
        assert children[1].value == 5 * WEI_PER_GEN
        assert children[0].nonce != children[1].nonce  # unique nonces


class TestOnFinalizedMessage:
    """On-finalized message: NOT debited at acceptance, debited at finalization."""

    @pytest.mark.asyncio
    async def test_finalized_message_not_debited_on_acceptance(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, _ = addrs
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)

        tx_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(value=4 * WEI_PER_GEN, on="finalized", address=child1),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        # Balance should NOT be debited yet (on_finalized, not on_accepted)
        assert accounts_manager.get_account_balance(contract) == 10 * WEI_PER_GEN

        # No children created for on_accepted (none exist)
        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT value FROM transactions WHERE triggered_by_hash = :h AND triggered_on = 'accepted'"
            ),
            {"h": tx_hash},
        ).fetchall()
        assert len(children) == 0


class TestMixedMessages:
    """Mix of on_accepted and on_finalized messages."""

    @pytest.mark.asyncio
    async def test_mixed_messages_only_accepted_debited(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, child2 = addrs
        _setup_contract(session, accounts_manager, contract, 20 * WEI_PER_GEN)

        tx_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(value=3 * WEI_PER_GEN, on="accepted", address=child1),
                _pending_tx(value=7 * WEI_PER_GEN, on="finalized", address=child2),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        # Only on_accepted debit: 20 - 3 = 17
        assert accounts_manager.get_account_balance(contract) == 17 * WEI_PER_GEN


class TestMessageFeeModes:
    """Fee-aware parent messages consume buckets and seed child fee accounting."""

    @pytest.mark.asyncio
    async def test_mode1_child_message_consumes_global_bucket_and_seeds_child(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, _ = addrs
        _setup_contract(session, accounts_manager, contract, 0)
        policy = StudioFeePolicy.from_env()
        fee_params = _encode_internal_fee_params()
        child_message_bucket = 15
        declared_budget = (
            _message_primary_fee(fee_params, policy) + child_message_bucket
        )
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=declared_budget),
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="accepted",
                    address=child1,
                    fee_params=fee_params,
                    declared_budget=declared_budget,
                    call_key="0x" + "12" * 32,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        parent = transactions_processor.get_transaction_by_hash(tx_hash)
        parent_accounting = parent["data"][FEE_ACCOUNTING_KEY]
        assert _amount(parent_accounting["message_fee_consumed"]) == declared_budget
        assert parent_accounting["allocation_consumed"] == {}

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT data, value FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchall()
        assert len(children) == 1
        child_data = children[0].data
        child_accounting = child_data[FEE_ACCOUNTING_KEY]
        assert _amount(child_data["fee_value"]) == declared_budget
        assert (
            _amount(child_data["fees_distribution"]["totalMessageFees"])
            == child_message_bucket
        )
        assert _amount(child_accounting["paid_fee_value"]) == declared_budget
        assert _amount(child_accounting["message_fee_budget"]) == child_message_bucket
        assert children[0].value == 0

    @pytest.mark.asyncio
    async def test_fee_aware_internal_message_without_declared_budget_is_rejected(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, _ = addrs
        _setup_contract(session, accounts_manager, contract, 0)
        fee_params = _encode_internal_fee_params()
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(
                total_message_fees=_message_primary_fee(
                    fee_params,
                    StudioFeePolicy.from_env(),
                )
            ),
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="accepted",
                    address=child1,
                    fee_params=fee_params,
                    declared_budget=0,
                    call_key="0x" + "12" * 32,
                )
            ]
        )

        with pytest.raises(RuntimeError, match="MessageDeclaredBudgetInsufficient"):
            await _run_tx(
                session,
                accounts_manager,
                transactions_processor,
                contract_processor,
                tx_hash,
                receipt,
            )

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT count(*) AS cnt FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert children.cnt == 0

    @pytest.mark.asyncio
    async def test_mode2_child_message_consumes_matching_allocation(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, _ = addrs
        _setup_contract(session, accounts_manager, contract, 0)
        policy = StudioFeePolicy.from_env()
        fee_params = _encode_internal_fee_params()
        call_key = "0x" + "34" * 32
        child_message_bucket = 15
        declared_budget = (
            _message_primary_fee(fee_params, policy) + child_message_bucket
        )
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=declared_budget),
            message_allocations=[
                _allocation(
                    recipient=child1,
                    call_key=call_key,
                    budget=declared_budget,
                    fee_params=fee_params,
                )
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="accepted",
                    address=child1,
                    fee_params=fee_params,
                    declared_budget=declared_budget,
                    call_key=call_key,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        parent = transactions_processor.get_transaction_by_hash(tx_hash)
        parent_accounting = parent["data"][FEE_ACCOUNTING_KEY]
        assert _amount(parent_accounting["message_fee_consumed"]) == declared_budget
        assert _amount_map(parent_accounting["allocation_consumed"]) == {
            "0": declared_budget
        }

    @pytest.mark.asyncio
    async def test_mode2_accepted_message_budget_is_consumed_again_on_reacceptance(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, _ = addrs
        _setup_contract(session, accounts_manager, contract, 0)
        policy = StudioFeePolicy.from_env()
        fee_params = _encode_internal_fee_params()
        call_key = "0x" + "89" * 32
        declared_budget = _message_primary_fee(fee_params, policy)
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(
                total_message_fees=declared_budget * 2,
                appeal_rounds=1,
            ),
            message_allocations=[
                _allocation(
                    recipient=child1,
                    call_key=call_key,
                    budget=declared_budget * 2,
                    fee_params=fee_params,
                )
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )
        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="accepted",
                    address=child1,
                    fee_params=fee_params,
                    declared_budget=declared_budget,
                    call_key=call_key,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )
        first = transactions_processor.get_transaction_by_hash(tx_hash)
        assert _amount_map(
            first["data"][FEE_ACCOUNTING_KEY]["allocation_consumed"]
        ) == {"0": declared_budget}

        transactions_processor.set_transaction_appeal(tx_hash, True)
        transactions_processor.set_transaction_contract_snapshot(tx_hash, None)
        transactions_processor.set_transaction_result(tx_hash, None)
        session.commit()

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        parent = transactions_processor.get_transaction_by_hash(tx_hash)
        parent_accounting = parent["data"][FEE_ACCOUNTING_KEY]
        assert _amount(parent_accounting["message_fee_consumed"]) == declared_budget * 2
        assert _amount_map(parent_accounting["allocation_consumed"]) == {
            "0": declared_budget * 2
        }

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT count(*) AS cnt FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert children.cnt == 2

    @pytest.mark.asyncio
    async def test_external_finalized_message_reserves_allocation_and_reimburses_gas(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, external_recipient, _ = addrs
        _setup_contract(session, accounts_manager, contract, 0)
        fee_params = _encode_external_fee_params(gas_limit=100, max_gas_price=10)
        call_key = "0x" + "45" * 32
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=1_000),
            message_allocations=[
                _external_allocation(
                    recipient=external_recipient,
                    call_key=call_key,
                    budget=1_000,
                    fee_params=fee_params,
                )
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="finalized",
                    address=external_recipient,
                    is_eth_send=True,
                    call_key=call_key,
                    gas_used=70,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )
        accepted = transactions_processor.get_transaction_by_hash(tx_hash)
        accepted_accounting = accepted["data"][FEE_ACCOUNTING_KEY]
        assert _amount(accepted_accounting["message_fee_consumed"]) == 0
        assert _amount(accepted_accounting["external_message_fee_reserved"]) == 100
        assert _amount(accepted_accounting["external_message_fee_reimbursed"]) == 0
        assert _amount(accepted_accounting["external_message_fee_remainder"]) == 0
        assert _amount_map(accepted_accounting["allocation_consumed"]) == {"0": 100}

        await _finalize_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
        )

        parent = transactions_processor.get_transaction_by_hash(tx_hash)
        parent_accounting = parent["data"][FEE_ACCOUNTING_KEY]
        assert _amount(parent_accounting["message_fee_consumed"]) == 70
        assert _amount(parent_accounting["external_message_fee_reserved"]) == 100
        assert _amount(parent_accounting["external_message_fee_reimbursed"]) == 70
        assert _amount(parent_accounting["external_message_fee_remainder"]) == 30
        assert _amount_map(parent_accounting["allocation_consumed"]) == {"0": 100}

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT type, value FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchall()
        assert len(children) == 1
        assert children[0].type == TransactionType.SEND.value
        assert children[0].value == 0

    @pytest.mark.asyncio
    async def test_external_finalized_message_caps_reimbursement_to_gas_limit(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, external_recipient, _ = addrs
        _setup_contract(session, accounts_manager, contract, 0)
        fee_params = _encode_external_fee_params(gas_limit=100, max_gas_price=10)
        call_key = "0x" + "46" * 32
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=1_000),
            message_allocations=[
                _external_allocation(
                    recipient=external_recipient,
                    call_key=call_key,
                    budget=1_000,
                    fee_params=fee_params,
                )
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="finalized",
                    address=external_recipient,
                    is_eth_send=True,
                    call_key=call_key,
                    gas_used=175,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )
        await _finalize_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
        )

        parent = transactions_processor.get_transaction_by_hash(tx_hash)
        parent_accounting = parent["data"][FEE_ACCOUNTING_KEY]
        assert parent_accounting["status"] == "settled"
        assert _amount(parent_accounting["message_fee_consumed"]) == 100
        assert _amount(parent_accounting["message_fee_refunded"]) == 900
        assert _amount(parent_accounting["external_message_fee_reserved"]) == 100
        assert _amount(parent_accounting["external_message_fee_reimbursed"]) == 100
        assert _amount(parent_accounting["external_message_fee_remainder"]) == 0
        assert _amount_map(parent_accounting["allocation_consumed"]) == {"0": 100}
        assert parent_accounting["external_message_events"][0]["gasUsed"] == 175

    @pytest.mark.asyncio
    async def test_value_bearing_external_message_debits_parent_and_credits_recipient_once(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, external_recipient, _ = addrs
        message_value = 3 * WEI_PER_GEN
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)
        fee_params = _encode_external_fee_params(gas_limit=100, max_gas_price=10)
        call_key = "0x" + "47" * 32
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=1_000),
            message_allocations=[
                _external_allocation(
                    recipient=external_recipient,
                    call_key=call_key,
                    budget=1_000,
                    fee_params=fee_params,
                )
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=message_value,
                    on="finalized",
                    address=external_recipient,
                    is_eth_send=True,
                    call_key=call_key,
                    gas_used=70,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )
        assert accounts_manager.get_account_balance(contract) == 10 * WEI_PER_GEN

        await _finalize_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
        )
        assert accounts_manager.get_account_balance(contract) == 7 * WEI_PER_GEN
        assert accounts_manager.get_account_balance(external_recipient) == 0

        parent = transactions_processor.get_transaction_by_hash(tx_hash)
        parent_accounting = parent["data"][FEE_ACCOUNTING_KEY]
        assert parent_accounting["status"] == "settled"
        assert _amount(parent_accounting["message_fee_consumed"]) == 70
        assert _amount(parent_accounting["message_fee_refunded"]) == 930
        assert _amount(parent_accounting["external_message_fee_reserved"]) == 100
        assert _amount(parent_accounting["external_message_fee_reimbursed"]) == 70
        assert _amount(parent_accounting["external_message_fee_remainder"]) == 30

        child = session.execute(
            __import__("sqlalchemy").text(
                "SELECT hash, type, value FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert child is not None
        assert child.type == TransactionType.SEND.value
        assert child.value == message_value

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            child.hash,
            _receipt(pending_transactions=[]),
        )
        assert accounts_manager.get_account_balance(contract) == 7 * WEI_PER_GEN
        assert accounts_manager.get_account_balance(external_recipient) == message_value

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            child.hash,
            _receipt(pending_transactions=[]),
        )
        assert accounts_manager.get_account_balance(contract) == 7 * WEI_PER_GEN
        assert accounts_manager.get_account_balance(external_recipient) == message_value

    @pytest.mark.asyncio
    async def test_on_acceptance_external_message_uses_legacy_fee_path(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, external_recipient, _ = addrs
        message_value = 2 * WEI_PER_GEN
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)
        fee_params = _encode_external_fee_params(gas_limit=100, max_gas_price=10)
        call_key = "0x" + "48" * 32
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=1_000),
            message_allocations=[
                _external_allocation(
                    recipient=external_recipient,
                    call_key=call_key,
                    budget=1_000,
                    fee_params=fee_params,
                )
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=message_value,
                    on="accepted",
                    address=external_recipient,
                    is_eth_send=True,
                    call_key=call_key,
                    gas_used=70,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        accepted = transactions_processor.get_transaction_by_hash(tx_hash)
        accepted_accounting = accepted["data"][FEE_ACCOUNTING_KEY]
        assert _amount(accepted_accounting["message_fee_consumed"]) == 0
        assert _amount(accepted_accounting["external_message_fee_reserved"]) == 0
        assert _amount_map(accepted_accounting["allocation_consumed"]) == {}
        assert accounts_manager.get_account_balance(contract) == 8 * WEI_PER_GEN
        assert accounts_manager.get_account_balance(external_recipient) == 0

        child = session.execute(
            __import__("sqlalchemy").text(
                "SELECT hash, type, value FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert child is not None
        assert child.type == TransactionType.SEND.value
        assert child.value == message_value

        await _finalize_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
        )
        finalized = transactions_processor.get_transaction_by_hash(tx_hash)
        finalized_accounting = finalized["data"][FEE_ACCOUNTING_KEY]
        assert finalized_accounting["status"] == "settled"
        assert _amount(finalized_accounting["message_fee_consumed"]) == 0
        assert _amount(finalized_accounting["message_fee_refunded"]) == 1_000
        assert accounts_manager.get_account_balance(contract) == 8 * WEI_PER_GEN

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            child.hash,
            _receipt(pending_transactions=[]),
        )
        assert accounts_manager.get_account_balance(external_recipient) == message_value

    @pytest.mark.asyncio
    async def test_external_message_freeze_exceeded_records_error_without_child_or_fee_consumption(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, external_recipient, _ = addrs
        _setup_contract(session, accounts_manager, contract, 1 * WEI_PER_GEN)
        fee_params = _encode_external_fee_params(gas_limit=100, max_gas_price=10)
        call_key = "0x" + "48" * 32
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=1_000),
            message_allocations=[
                _external_allocation(
                    recipient=external_recipient,
                    call_key=call_key,
                    budget=1_000,
                    fee_params=fee_params,
                )
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=3 * WEI_PER_GEN,
                    on="finalized",
                    address=external_recipient,
                    is_eth_send=True,
                    call_key=call_key,
                    gas_used=70,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        parent = transactions_processor.get_transaction_by_hash(tx_hash)
        leader_receipt = parent["consensus_data"]["leader_receipt"][0]
        parent_accounting = parent["data"][FEE_ACCOUNTING_KEY]
        assert leader_receipt["execution_result"] == ExecutionResultStatus.ERROR.value
        assert leader_receipt["pending_transactions"] == []
        assert (
            leader_receipt["genvm_result"]["error_code"]
            == "EXTERNAL_MESSAGE_FREEZE_EXCEEDED"
        )
        assert _amount(parent_accounting["message_fee_consumed"]) == 0
        assert _amount(parent_accounting["external_message_fee_reserved"]) == 0
        assert accounts_manager.get_account_balance(contract) == 1 * WEI_PER_GEN

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT count(*) AS cnt FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert children.cnt == 0

        await _finalize_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
        )

        finalized = transactions_processor.get_transaction_by_hash(tx_hash)
        finalized_accounting = finalized["data"][FEE_ACCOUNTING_KEY]
        assert finalized_accounting["status"] == "settled"
        assert _amount(finalized_accounting["message_fee_consumed"]) == 0
        assert _amount(finalized_accounting["message_fee_refunded"]) == 1_000
        assert accounts_manager.get_account_balance(contract) == 1 * WEI_PER_GEN

    @pytest.mark.asyncio
    async def test_error_result_ignores_external_messages_without_child_fee_or_value_drain(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, external_recipient, _ = addrs
        _setup_contract(session, accounts_manager, contract, 2 * WEI_PER_GEN)
        fee_params = _encode_external_fee_params(gas_limit=100, max_gas_price=10)
        call_key = "0x" + "93" * 32
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=1_000),
            message_allocations=[
                _external_allocation(
                    recipient=external_recipient,
                    call_key=call_key,
                    budget=1_000,
                    fee_params=fee_params,
                )
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=1 * WEI_PER_GEN,
                    on="finalized",
                    address=external_recipient,
                    is_eth_send=True,
                    call_key=call_key,
                    gas_used=70,
                )
            ],
            execution_result=ExecutionResultStatus.ERROR,
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        accepted = transactions_processor.get_transaction_by_hash(tx_hash)
        accepted_accounting = accepted["data"][FEE_ACCOUNTING_KEY]
        assert _amount(accepted_accounting["message_fee_consumed"]) == 0
        assert _amount(accepted_accounting["external_message_fee_reserved"]) == 0
        assert _amount_map(accepted_accounting["allocation_consumed"]) == {}
        assert accounts_manager.get_account_balance(contract) == 2 * WEI_PER_GEN

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT count(*) AS cnt FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert children.cnt == 0

        await _finalize_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
        )

        finalized = transactions_processor.get_transaction_by_hash(tx_hash)
        finalized_accounting = finalized["data"][FEE_ACCOUNTING_KEY]
        assert finalized_accounting["status"] == "settled"
        assert _amount(finalized_accounting["message_fee_consumed"]) == 0
        assert _amount(finalized_accounting["message_fee_refunded"]) == 1_000
        assert accounts_manager.get_account_balance(contract) == 2 * WEI_PER_GEN

    @pytest.mark.asyncio
    async def test_external_freeze_counts_other_accepted_finalized_external_values(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, _, _, _ = addrs
        contract = "0x" + secrets.token_hex(20)
        external_recipient = "0x" + secrets.token_hex(20)
        contract = accounts_manager.create_new_account_with_address(contract).id
        external_recipient = accounts_manager.create_new_account_with_address(
            external_recipient
        ).id
        session.commit()
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)

        tx_hash_a = _insert_tx(transactions_processor, session, sender, contract)
        receipt_a = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=7 * WEI_PER_GEN,
                    on="finalized",
                    address=external_recipient,
                    is_eth_send=True,
                )
            ]
        )
        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash_a,
            receipt_a,
        )
        assert accounts_manager.get_account_balance(contract) == 10 * WEI_PER_GEN

        tx_hash_b = _insert_tx(transactions_processor, session, sender, contract)
        receipt_b = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=5 * WEI_PER_GEN,
                    on="finalized",
                    address=external_recipient,
                    is_eth_send=True,
                )
            ]
        )
        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash_b,
            receipt_b,
        )

        second = transactions_processor.get_transaction_by_hash(tx_hash_b)
        leader_receipt = second["consensus_data"]["leader_receipt"][0]
        assert leader_receipt["execution_result"] == ExecutionResultStatus.ERROR.value
        assert (
            leader_receipt["genvm_result"]["error_code"]
            == "EXTERNAL_MESSAGE_FREEZE_EXCEEDED"
        )
        freeze = leader_receipt["genvm_result"]["external_message_freeze"]
        assert int(freeze["declaredValue"]) == 5 * WEI_PER_GEN
        assert int(freeze["balance"]) == 10 * WEI_PER_GEN
        assert int(freeze["reservedExternal"]) == 7 * WEI_PER_GEN
        assert int(freeze["availableLimit"]) == 3 * WEI_PER_GEN
        assert accounts_manager.get_account_balance(contract) == 10 * WEI_PER_GEN

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT count(*) AS cnt FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash_b},
        ).fetchone()
        assert children.cnt == 0

    @pytest.mark.asyncio
    async def test_other_accepted_external_freeze_limits_internal_value_backing(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, _, _, _ = addrs
        contract = "0x" + secrets.token_hex(20)
        internal_recipient = "0x" + secrets.token_hex(20)
        external_recipient = "0x" + secrets.token_hex(20)
        contract = accounts_manager.create_new_account_with_address(contract).id
        internal_recipient = accounts_manager.create_new_account_with_address(
            internal_recipient
        ).id
        external_recipient = accounts_manager.create_new_account_with_address(
            external_recipient
        ).id
        session.commit()
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)

        tx_hash_a = _insert_tx(transactions_processor, session, sender, contract)
        receipt_a = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=7 * WEI_PER_GEN,
                    on="finalized",
                    address=external_recipient,
                    is_eth_send=True,
                )
            ]
        )
        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash_a,
            receipt_a,
        )

        tx_hash_b = _insert_tx(transactions_processor, session, sender, contract)
        receipt_b = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=5 * WEI_PER_GEN,
                    on="accepted",
                    address=internal_recipient,
                )
            ]
        )
        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash_b,
            receipt_b,
        )

        assert accounts_manager.get_account_balance(contract) == 10 * WEI_PER_GEN
        child = session.execute(
            __import__("sqlalchemy").text(
                "SELECT type, value FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash_b},
        ).fetchone()
        assert child is not None
        assert child.type == TransactionType.RUN_CONTRACT.value
        assert child.value == 0

    @pytest.mark.asyncio
    async def test_external_freeze_keeps_finalized_value_available_when_internal_value_unbacked(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, internal_recipient, external_recipient = addrs
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)
        policy = StudioFeePolicy.from_env()
        internal_fee_params = _encode_internal_fee_params()
        internal_declared_budget = (
            _message_primary_fee(internal_fee_params, policy) + 15
        )
        external_fee_params = _encode_external_fee_params(
            gas_limit=100,
            max_gas_price=10,
        )
        internal_call_key = "0x" + "49" * 32
        external_call_key = "0x" + "50" * 32
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=internal_declared_budget + 1_000),
            message_allocations=[
                _allocation(
                    recipient=internal_recipient,
                    call_key=internal_call_key,
                    budget=internal_declared_budget,
                    fee_params=internal_fee_params,
                ),
                _external_allocation(
                    recipient=external_recipient,
                    call_key=external_call_key,
                    budget=1_000,
                    fee_params=external_fee_params,
                ),
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=5 * WEI_PER_GEN,
                    on="accepted",
                    address=internal_recipient,
                    call_key=internal_call_key,
                ),
                _pending_tx(
                    value=7 * WEI_PER_GEN,
                    on="finalized",
                    address=external_recipient,
                    is_eth_send=True,
                    call_key=external_call_key,
                    gas_used=70,
                ),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        assert accounts_manager.get_account_balance(contract) == 10 * WEI_PER_GEN
        accepted = transactions_processor.get_transaction_by_hash(tx_hash)
        accepted_accounting = accepted["data"][FEE_ACCOUNTING_KEY]
        assert (
            _amount(accepted_accounting["message_fee_consumed"])
            == internal_declared_budget
        )

        accepted_child = session.execute(
            __import__("sqlalchemy").text(
                "SELECT type, value, data FROM transactions "
                "WHERE triggered_by_hash = :h AND triggered_on = 'accepted'"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert accepted_child is not None
        assert accepted_child.type == TransactionType.RUN_CONTRACT.value
        assert accepted_child.value == 0
        assert _amount(accepted_child.data["user_value"]) == 0

        await _finalize_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
        )

        assert accounts_manager.get_account_balance(contract) == 3 * WEI_PER_GEN
        finalized = transactions_processor.get_transaction_by_hash(tx_hash)
        finalized_accounting = finalized["data"][FEE_ACCOUNTING_KEY]
        assert finalized_accounting["status"] == "settled"
        assert (
            _amount(finalized_accounting["message_fee_consumed"])
            == internal_declared_budget + 70
        )
        assert _amount(finalized_accounting["external_message_fee_reserved"]) == 100
        assert _amount(finalized_accounting["external_message_fee_reimbursed"]) == 70
        assert _amount(finalized_accounting["external_message_fee_remainder"]) == 30

        finalized_child = session.execute(
            __import__("sqlalchemy").text(
                "SELECT type, value FROM transactions "
                "WHERE triggered_by_hash = :h AND triggered_on = 'finalized'"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert finalized_child is not None
        assert finalized_child.type == TransactionType.SEND.value
        assert finalized_child.value == 7 * WEI_PER_GEN

    @pytest.mark.asyncio
    async def test_finalized_internal_message_consumes_bucket_and_seeds_child_on_finalize(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, _ = addrs
        _setup_contract(session, accounts_manager, contract, 0)
        policy = StudioFeePolicy.from_env()
        fee_params = _encode_internal_fee_params()
        child_message_bucket = 15
        declared_budget = (
            _message_primary_fee(fee_params, policy) + child_message_bucket
        )
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=declared_budget),
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="finalized",
                    address=child1,
                    fee_params=fee_params,
                    declared_budget=declared_budget,
                    call_key="0x" + "67" * 32,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )
        accepted = transactions_processor.get_transaction_by_hash(tx_hash)
        assert (
            _amount(accepted["data"][FEE_ACCOUNTING_KEY]["message_fee_consumed"])
            == declared_budget
        )

        await _finalize_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
        )

        parent = transactions_processor.get_transaction_by_hash(tx_hash)
        parent_accounting = parent["data"][FEE_ACCOUNTING_KEY]
        assert parent_accounting["status"] == "settled"
        assert _amount(parent_accounting["message_fee_consumed"]) == declared_budget
        assert _amount(parent_accounting["message_fee_refunded"]) == 0

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT data, value FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchall()
        assert len(children) == 1
        child_data = children[0].data
        child_accounting = child_data[FEE_ACCOUNTING_KEY]
        assert _amount(child_data["fee_value"]) == declared_budget
        assert (
            _amount(child_data["fees_distribution"]["totalMessageFees"])
            == child_message_bucket
        )
        assert _amount(child_accounting["paid_fee_value"]) == declared_budget
        assert _amount(child_accounting["message_fee_budget"]) == child_message_bucket
        assert children[0].value == 0

    @pytest.mark.asyncio
    async def test_error_rereveal_preserves_accepted_message_fee_and_refunds_finalized_fee(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, child2 = addrs
        _setup_contract(session, accounts_manager, contract, 0)
        policy = StudioFeePolicy.from_env()
        accepted_fee_params = _encode_internal_fee_params()
        finalized_fee_params = _encode_internal_fee_params()
        accepted_declared_budget = _message_primary_fee(accepted_fee_params, policy)
        finalized_declared_budget = _message_primary_fee(finalized_fee_params, policy)
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(
                total_message_fees=accepted_declared_budget + finalized_declared_budget
            ),
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )
        first_receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="accepted",
                    address=child1,
                    fee_params=accepted_fee_params,
                    declared_budget=accepted_declared_budget,
                    call_key="0x" + "91" * 32,
                ),
                _pending_tx(
                    value=0,
                    on="finalized",
                    address=child2,
                    fee_params=finalized_fee_params,
                    declared_budget=finalized_declared_budget,
                    call_key="0x" + "92" * 32,
                ),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            first_receipt,
        )

        accepted = transactions_processor.get_transaction_by_hash(tx_hash)
        accepted_accounting = accepted["data"][FEE_ACCOUNTING_KEY]
        assert _amount(accepted_accounting["message_fee_consumed"]) == (
            accepted_declared_budget + finalized_declared_budget
        )

        transactions_processor.set_transaction_appeal(tx_hash, True)
        transactions_processor.set_transaction_contract_snapshot(tx_hash, None)
        transactions_processor.set_transaction_result(tx_hash, None)
        session.commit()

        error_receipt = _receipt(
            pending_transactions=[],
            execution_result=ExecutionResultStatus.ERROR,
        )
        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            error_receipt,
        )

        rerevealed = transactions_processor.get_transaction_by_hash(tx_hash)
        rerevealed_accounting = rerevealed["data"][FEE_ACCOUNTING_KEY]
        assert (
            _amount(rerevealed_accounting["message_fee_consumed"])
            == accepted_declared_budget
        )

        await _finalize_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
        )

        finalized = transactions_processor.get_transaction_by_hash(tx_hash)
        finalized_accounting = finalized["data"][FEE_ACCOUNTING_KEY]
        assert finalized_accounting["status"] == "settled"
        assert (
            _amount(finalized_accounting["message_fee_consumed"])
            == accepted_declared_budget
        )
        assert (
            _amount(finalized_accounting["message_fee_refunded"])
            == finalized_declared_budget
        )

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT triggered_on FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchall()
        assert [child.triggered_on for child in children] == ["accepted"]

    @pytest.mark.asyncio
    async def test_mode2_child_message_installs_allocation_subtree_for_grandchild(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, child2 = addrs
        _setup_contract(session, accounts_manager, contract, 0)
        policy = StudioFeePolicy.from_env()
        parent_fee_params = _encode_internal_fee_params()
        child_fee_params = _encode_internal_fee_params()
        parent_call_key = "0x" + "56" * 32
        grandchild_call_key = "0x" + "78" * 32
        grandchild_declared_budget = _message_primary_fee(child_fee_params, policy)
        parent_declared_budget = (
            _message_primary_fee(parent_fee_params, policy) + grandchild_declared_budget
        )
        root_allocation = _allocation(
            recipient=child1,
            call_key=parent_call_key,
            budget=parent_declared_budget,
            fee_params=parent_fee_params,
        )
        child_allocation = {
            **_allocation(
                recipient=child2,
                call_key=grandchild_call_key,
                budget=grandchild_declared_budget,
                fee_params="0x" + child_fee_params.hex(),
            ),
            "parentIndex": 0,
        }
        child_expected_allocation = _allocation(
            recipient=child2,
            call_key=grandchild_call_key,
            budget=grandchild_declared_budget,
            fee_params="0x" + child_fee_params.hex(),
        )
        accounting = _create_fee_accounting(
            sender,
            _fees_distribution(total_message_fees=parent_declared_budget),
            message_allocations=[
                root_allocation,
                child_allocation,
            ],
        )
        tx_hash = _insert_tx(
            transactions_processor,
            session,
            sender,
            contract,
            data={
                "calldata": base64.b64encode(b"\x06").decode("ascii"),
                FEE_ACCOUNTING_KEY: accounting,
            },
        )

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="accepted",
                    address=child1,
                    fee_params=parent_fee_params,
                    declared_budget=parent_declared_budget,
                    call_key=parent_call_key,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        parent = transactions_processor.get_transaction_by_hash(tx_hash)
        parent_accounting = parent["data"][FEE_ACCOUNTING_KEY]
        assert (
            _amount(parent_accounting["message_fee_consumed"]) == parent_declared_budget
        )
        assert _amount_map(parent_accounting["allocation_consumed"]) == {
            "0": parent_declared_budget
        }

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT hash AS tx_hash, data FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchall()
        assert len(children) == 1
        child_hash = children[0].tx_hash
        child_data = children[0].data
        child_accounting = child_data[FEE_ACCOUNTING_KEY]
        assert (
            _amount(child_data["fees_distribution"]["totalMessageFees"])
            == grandchild_declared_budget
        )
        assert child_data["message_allocations_count"] == 1
        assert (
            _amount(child_accounting["message_fee_budget"])
            == grandchild_declared_budget
        )
        assert child_accounting["message_allocations"][0] == child_expected_allocation
        assert child_accounting["message_allocations"][0]["recipient"] == child2.lower()

        _setup_contract(session, accounts_manager, child1, 0)
        child_receipt = _receipt(
            pending_transactions=[
                _pending_tx(
                    value=0,
                    on="accepted",
                    address=child2,
                    fee_params=child_fee_params,
                    declared_budget=grandchild_declared_budget,
                    call_key=grandchild_call_key,
                )
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            child_hash,
            child_receipt,
        )

        child = transactions_processor.get_transaction_by_hash(child_hash)
        updated_child_accounting = child["data"][FEE_ACCOUNTING_KEY]
        assert (
            _amount(updated_child_accounting["message_fee_consumed"])
            == grandchild_declared_budget
        )
        assert _amount_map(updated_child_accounting["allocation_consumed"]) == {
            "0": grandchild_declared_budget
        }


class TestZeroValueTransaction:
    """Zero-value tx through consensus: no balance changes, no children."""

    @pytest.mark.asyncio
    async def test_zero_value_no_side_effects(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, _, _ = addrs
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)

        tx_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(pending_transactions=[])

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        assert accounts_manager.get_account_balance(contract) == 10 * WEI_PER_GEN

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT count(*) as cnt FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert children.cnt == 0


class TestChildActivationCredit:
    """Child tx with value credits the target contract via credit_tx_value_once."""

    @pytest.mark.asyncio
    async def test_child_credited_on_activation(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child_target, _ = addrs
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)

        # Create parent tx that emits child with value
        parent_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(value=3 * WEI_PER_GEN, on="accepted", address=child_target),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            parent_hash,
            receipt,
        )

        # Parent debited
        assert accounts_manager.get_account_balance(contract) == 7 * WEI_PER_GEN

        # Find the child tx
        child_row = session.execute(
            __import__("sqlalchemy").text(
                "SELECT hash, value FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": parent_hash},
        ).fetchone()
        assert child_row is not None
        assert child_row.value == 3 * WEI_PER_GEN

        # Set up child target as a contract so it can receive value
        from backend.database_handler.models import CurrentState

        child_contract = session.query(CurrentState).filter_by(id=child_target).one()
        child_contract.data = {
            "state": {
                "accepted": {"slot": base64.b64encode(b"child").decode()},
                "finalized": {},
            }
        }
        session.commit()

        # Execute child tx — credit_tx_value_once should credit child_target
        child_receipt = _receipt(pending_transactions=[])
        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            child_row.hash,
            child_receipt,
        )

        # Child target should be credited with 3 GEN
        assert accounts_manager.get_account_balance(child_target) == 3 * WEI_PER_GEN


class TestMintOnDemand:
    """Sender with insufficient balance gets topped up automatically."""

    def test_mint_shortfall(self, session, accounts_manager, addrs):
        sender, _, _, _ = addrs
        # Sender starts with 0
        assert accounts_manager.get_account_balance(sender) == 0

        value = 100 * WEI_PER_GEN
        sender_balance = accounts_manager.get_account_balance(sender)
        if sender_balance < value:
            shortfall = value - sender_balance
            accounts_manager.credit_account_balance(sender, shortfall)

        accounts_manager.debit_account_balance(sender, value)
        session.commit()

        assert accounts_manager.get_account_balance(sender) == 0


class TestCancelRefund:
    """Payable tx canceled before activation: sender refunded."""

    def test_cancel_refunds_uncredited_value(self, session, accounts_manager, addrs):
        sender, contract, _, _ = addrs
        accounts_manager.update_account_balance(sender, 100 * WEI_PER_GEN)
        session.commit()

        value = 10 * WEI_PER_GEN

        # Simulate submission debit
        accounts_manager.debit_account_balance(sender, value)
        session.commit()
        assert accounts_manager.get_account_balance(sender) == 90 * WEI_PER_GEN

        # Simulate refund (value_credited is false, so refund succeeds)
        accounts_manager.credit_account_balance(sender, value)
        session.commit()
        assert accounts_manager.get_account_balance(sender) == 100 * WEI_PER_GEN


class TestCreditIdempotency:
    """credit_tx_value_once is idempotent — second call returns False."""

    def test_double_credit_prevented(
        self, session, accounts_manager, transactions_processor, addrs
    ):
        sender, contract, _, _ = addrs
        tx_hash = _insert_tx(
            transactions_processor, session, sender, contract, value=5 * WEI_PER_GEN
        )

        # First credit
        result1 = accounts_manager.credit_tx_value_once(
            tx_hash, contract, 5 * WEI_PER_GEN
        )
        session.commit()
        assert result1 is True
        assert accounts_manager.get_account_balance(contract) == 5 * WEI_PER_GEN

        # Second credit — should be rejected
        result2 = accounts_manager.credit_tx_value_once(
            tx_hash, contract, 5 * WEI_PER_GEN
        )
        session.commit()
        assert result2 is False
        assert (
            accounts_manager.get_account_balance(contract) == 5 * WEI_PER_GEN
        )  # unchanged
