from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.protocol_rpc import endpoints
from backend.errors.errors import InvalidAddressError
from backend.domain.types import LLMProvider, TransactionType
from backend.database_handler.models import TransactionStatus
from backend.protocol_rpc.types import (
    DecodedRollupTransaction,
    DecodedRollupTransactionData,
    DecodedRollupTransactionDataArgs,
    DecodedFinalizeTransactionDataArgs,
    DecodedsubmitAppealDataArgs,
    DecodedTopUpFeesDataArgs,
)


def test_top_level_deploy_uses_transactional_virtual_ghost_factory():
    processor = MagicMock()
    processor.get_successful_ghost_creation_count.return_value = 0
    processor.is_genvm_contract_address.return_value = False

    address = endpoints._allocate_top_level_ghost_address(
        processor,
        42,
        "0x1111111111111111111111111111111111111111",
    )

    assert address == "0x25A58acd32f777db380EA378cCE191972aa62c5e"
    processor.lock_ghost_factory.assert_called_once_with()
    processor.get_successful_ghost_creation_count.assert_called_once_with()
    processor.is_genvm_contract_address.assert_called_once_with(address)


def test_top_level_deploy_rejects_reused_create2_address():
    processor = MagicMock()
    processor.get_successful_ghost_creation_count.return_value = 4
    processor.is_genvm_contract_address.return_value = True

    with pytest.raises(endpoints.InvalidTransactionError, match="GhostAlreadyDeployed"):
        endpoints._allocate_top_level_ghost_address(
            processor,
            42,
            "0x1111111111111111111111111111111111111111",
        )


def test_fund_account_uses_request_scoped_session(monkeypatch):
    session = object()
    accounts_manager_instance = MagicMock()
    accounts_manager_instance.is_valid_address.return_value = True

    transactions_processor_instance = MagicMock()
    transactions_processor_instance.get_transaction_count.return_value = 12
    transactions_processor_instance.insert_transaction.return_value = None

    # Mock secrets.token_hex to return a predictable hash
    def mock_token_hex(n):
        return "abc" if n == 32 else "xxx"

    monkeypatch.setattr("secrets.token_hex", mock_token_hex)

    monkeypatch.setattr(
        endpoints,
        "AccountsManager",
        lambda s: accounts_manager_instance if s is session else None,
    )
    monkeypatch.setattr(
        endpoints,
        "TransactionsProcessor",
        lambda s: transactions_processor_instance if s is session else None,
    )

    result = endpoints.fund_account(session, "0x" + "1" * 40, 25)

    assert result == "0xabc"
    accounts_manager_instance.is_valid_address.assert_called_once_with("0x" + "1" * 40)
    transactions_processor_instance.get_transaction_count.assert_called_once_with(None)
    transactions_processor_instance.insert_transaction.assert_called_once_with(
        None, "0x" + "1" * 40, None, 25, 0, 12, False, 0, None, "0xabc"
    )


def test_fund_account_raises_for_invalid_address(monkeypatch):
    session = object()
    accounts_manager_instance = MagicMock()
    accounts_manager_instance.is_valid_address.return_value = False

    monkeypatch.setattr(
        endpoints, "AccountsManager", lambda _session: accounts_manager_instance
    )
    monkeypatch.setattr(
        endpoints, "TransactionsProcessor", lambda _session: MagicMock()
    )

    with pytest.raises(InvalidAddressError):
        endpoints.fund_account(session, "0x" + "2" * 40, 10)


def test_fund_account_instantiates_managers_per_session(monkeypatch):
    sessions_seen = []
    managers_created = []

    def fake_accounts_manager(session):
        mgr = MagicMock()
        mgr.is_valid_address.return_value = True
        sessions_seen.append(session)
        managers_created.append(mgr)
        return mgr

    def fake_transactions_processor(_session):
        proc = MagicMock()
        proc.get_transaction_count.return_value = 1
        proc.insert_transaction.return_value = "0xhash"
        managers_created.append(proc)
        return proc

    monkeypatch.setattr(endpoints, "AccountsManager", fake_accounts_manager)
    monkeypatch.setattr(endpoints, "TransactionsProcessor", fake_transactions_processor)

    session_one = object()
    session_two = object()

    endpoints.fund_account(session_one, "0x" + "3" * 40, 5)
    endpoints.fund_account(session_two, "0x" + "4" * 40, 7)

    assert sessions_seen == [session_one, session_two]
    # Ensure we created distinct manager/processor pairs per call
    assert len(managers_created) == 4
    assert managers_created[0] is not managers_created[2]
    assert managers_created[1] is not managers_created[3]


def test_add_provider_uses_request_scoped_session(monkeypatch):
    session = object()
    registry_instance = MagicMock()
    captured_sessions = []

    monkeypatch.setattr(endpoints, "validate_provider", lambda _provider: None)
    monkeypatch.setattr(
        endpoints,
        "LLMProviderRegistry",
        lambda s: captured_sessions.append(s) or registry_instance,
    )

    params = {
        "provider": "prov",
        "model": "model",
        "config": {"a": 1},
        "plugin": "plugin",
        "plugin_config": {"url": "https://example"},
    }
    registry_instance.add.return_value = 7

    result = endpoints.add_provider(session, params)

    assert result == 7
    assert captured_sessions == [session]
    registry_instance.add.assert_called_once()
    added_provider = registry_instance.add.call_args[0][0]
    assert isinstance(added_provider, LLMProvider)
    assert added_provider.provider == "prov"


def test_update_provider_uses_request_scoped_session(monkeypatch):
    session = object()
    registry_instance = MagicMock()
    captured_sessions = []

    monkeypatch.setattr(endpoints, "validate_provider", lambda _provider: None)
    monkeypatch.setattr(
        endpoints,
        "LLMProviderRegistry",
        lambda s: captured_sessions.append(s) or registry_instance,
    )

    params = {
        "provider": "prov",
        "model": "model",
        "config": {"a": 1},
        "plugin": "plugin",
        "plugin_config": {"url": "https://example"},
    }

    endpoints.update_provider(session, 3, params)

    assert captured_sessions == [session]
    registry_instance.update.assert_called_once()
    assert registry_instance.update.call_args[0][0] == 3
    updated_provider = registry_instance.update.call_args[0][1]
    assert isinstance(updated_provider, LLMProvider)
    assert updated_provider.provider == "prov"


def test_delete_provider_uses_request_scoped_session(monkeypatch):
    session = object()
    registry_instance = MagicMock()
    captured_sessions = []

    monkeypatch.setattr(
        endpoints,
        "LLMProviderRegistry",
        lambda s: captured_sessions.append(s) or registry_instance,
    )

    endpoints.delete_provider(session, 5)

    assert captured_sessions == [session]
    registry_instance.delete.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_create_validator_uses_request_scoped_session(monkeypatch):
    session = object()
    accounts_manager_instance = MagicMock()
    account = SimpleNamespace(address="0xabc", key="priv")
    accounts_manager_instance.create_new_account.return_value = account
    registry_instance = SimpleNamespace(
        create_validator=AsyncMock(return_value={"address": account.address})
    )

    validators_manager = SimpleNamespace(registry=registry_instance)

    monkeypatch.setattr(endpoints, "validate_provider", lambda _provider: None)
    monkeypatch.setattr(
        endpoints,
        "AccountsManager",
        lambda s: accounts_manager_instance if s is session else None,
    )
    monkeypatch.setattr(
        endpoints,
        "get_default_provider_for",
        lambda provider, model: LLMProvider(
            provider=provider,
            model=model,
            config={},
            plugin="plugin",
            plugin_config={},
        ),
    )

    result = await endpoints.create_validator(
        session,
        validators_manager,
        stake=10,
        provider="prov",
        model="model",
    )

    assert result == {"address": "0xabc"}
    accounts_manager_instance.create_new_account.assert_called_once()
    registry_instance.create_validator.assert_awaited_once()
    validator_arg = registry_instance.create_validator.await_args.args[0]
    assert validator_arg.address == "0xabc"
    assert validator_arg.stake == 10


@pytest.mark.asyncio
async def test_replace_validators_is_atomic_and_uses_request_session(monkeypatch):
    session = object()
    accounts_manager_instance = MagicMock()
    accounts_manager_instance.create_new_account.side_effect = [
        SimpleNamespace(address="0x1", key="k1"),
        SimpleNamespace(address="0x2", key="k2"),
    ]
    registry_instance = SimpleNamespace(
        replace_all_validators=AsyncMock(
            return_value=[{"address": "0x1"}, {"address": "0x2"}]
        )
    )
    validators_manager = SimpleNamespace(registry=registry_instance)

    monkeypatch.setattr(endpoints, "validate_provider", lambda _provider: None)
    monkeypatch.setattr(
        endpoints,
        "AccountsManager",
        lambda s: accounts_manager_instance if s is session else None,
    )

    validator_config = {
        "stake": 8,
        "provider": "openrouter",
        "model": "test-model",
        "config": {"temperature": 0.75},
        "plugin": "openai-compatible",
        "plugin_config": {
            "api_key_env_var": "OPENROUTERAPIKEY",
            "api_url": "https://openrouter.ai/api",
            "mock_response": {},
        },
    }
    result = await endpoints.replace_validators(
        session,
        validators_manager,
        [validator_config, validator_config],
    )

    assert result == [{"address": "0x1"}, {"address": "0x2"}]
    registry_instance.replace_all_validators.assert_awaited_once()
    replacements = registry_instance.replace_all_validators.await_args.args[0]
    assert [validator.address for validator in replacements] == ["0x1", "0x2"]
    assert [validator.stake for validator in replacements] == [8, 8]


@pytest.mark.asyncio
async def test_create_random_validators_use_request_session(monkeypatch):
    session = object()
    accounts_manager_instance = MagicMock()
    accounts_created = [
        SimpleNamespace(address="0x1", key="k1"),
        SimpleNamespace(address="0x2", key="k2"),
    ]
    accounts_manager_instance.create_new_account.side_effect = accounts_created

    registry_instance = SimpleNamespace(
        create_validator=AsyncMock(
            side_effect=[
                {"address": "0x1"},
                {"address": "0x2"},
            ]
        )
    )

    validators_manager = SimpleNamespace(registry=registry_instance)

    class FakeLLMRegistry:
        def __init__(self, session_arg):
            assert session_arg is session

        def get_all(self):
            return []

    async def fake_random_validator_config(
        get_all_fn,
        availability_fn,
        limit_providers,
        limit_models,
        amount,
    ):
        assert get_all_fn() == []
        assert amount == 2
        return [
            LLMProvider(
                provider="prov",
                model="model",
                config={},
                plugin="plugin",
                plugin_config={},
            )
            for _ in range(amount)
        ]

    def accounts_manager_factory(s):
        assert s is session
        return accounts_manager_instance

    # Mock check_provider_is_available to just return True
    async def fake_check_provider_is_available(provider, model):
        return True

    monkeypatch.setattr(endpoints, "AccountsManager", accounts_manager_factory)
    monkeypatch.setattr(endpoints, "LLMProviderRegistry", FakeLLMRegistry)
    monkeypatch.setattr(
        endpoints, "random_validator_config", fake_random_validator_config
    )
    monkeypatch.setattr(
        endpoints, "check_provider_is_available", fake_check_provider_is_available
    )

    genvm_manager = MagicMock()

    response = await endpoints.create_random_validators(
        session,
        validators_manager,
        genvm_manager,
        count=2,
        min_stake=5,
        max_stake=5,
    )

    assert response == [{"address": "0x1"}, {"address": "0x2"}]
    assert accounts_manager_instance.create_new_account.call_count == 2
    assert registry_instance.create_validator.await_count == 2


@pytest.mark.asyncio
async def test_update_validator_uses_request_session(monkeypatch):
    session = object()
    registry_instance = SimpleNamespace(
        update_validator=AsyncMock(return_value={"address": "0xabc"})
    )

    validators_manager = SimpleNamespace(registry=registry_instance)

    monkeypatch.setattr(endpoints, "validate_provider", lambda _provider: None)
    monkeypatch.setattr(
        endpoints,
        "get_default_provider_for",
        lambda provider, model: LLMProvider(
            provider=provider,
            model=model,
            config={},
            plugin="plugin",
            plugin_config={},
        ),
    )

    result = await endpoints.update_validator(
        session,
        validators_manager,
        validator_address="0xabc",
        stake=42,
        provider="prov",
        model="model",
    )

    assert result == {"address": "0xabc"}
    registry_instance.update_validator.assert_awaited_once()
    validator_arg = registry_instance.update_validator.await_args.args[0]
    assert validator_arg.address == "0xabc"
    assert validator_arg.stake == 42


@pytest.mark.asyncio
async def test_delete_validator_uses_request_session(monkeypatch):
    registry_instance = SimpleNamespace(delete_validator=AsyncMock())

    validators_manager = SimpleNamespace(registry=registry_instance)

    result = await endpoints.delete_validator(validators_manager, "0xabc")

    assert result == "0xabc"
    registry_instance.delete_validator.assert_awaited_once_with("0xabc")


@pytest.mark.asyncio
async def test_delete_all_validators_uses_request_session(monkeypatch):
    registry_instance = SimpleNamespace(
        delete_all_validators=AsyncMock(),
        get_all_validators=MagicMock(return_value=[]),
    )

    validators_manager = SimpleNamespace(registry=registry_instance)

    result = await endpoints.delete_all_validators(validators_manager)

    assert result == []
    registry_instance.delete_all_validators.assert_awaited_once()
    registry_instance.get_all_validators.assert_called_once()


def test_send_raw_transaction_uses_request_session(monkeypatch):
    session = object()
    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True
    transactions_processor = MagicMock()
    transactions_processor.get_transaction_by_hash.return_value = None
    transactions_processor.insert_transaction.return_value = None
    transactions_processor.begin_evm_envelope.return_value = None

    constructed = []

    def accounts_manager_factory(s):
        assert s is session
        constructed.append("accounts_manager")
        return accounts_manager

    def transactions_processor_factory(s):
        assert s is session
        constructed.append("transactions_processor")
        return transactions_processor

    monkeypatch.setattr(endpoints, "AccountsManager", accounts_manager_factory)
    monkeypatch.setattr(
        endpoints, "TransactionsProcessor", transactions_processor_factory
    )

    decoded = SimpleNamespace(
        from_address="0x" + "1" * 40,
        value=0,
        data=object(),
        to_address="0x" + "2" * 40,
        nonce=1,
    )
    genlayer_tx = SimpleNamespace(
        type=TransactionType.SEND,
        from_address=decoded.from_address,
        max_rotations=0,
        num_of_initial_validators=1,
        data=SimpleNamespace(),
    )

    transactions_parser = MagicMock()
    transactions_parser.decode_signed_transaction.return_value = decoded
    transactions_parser.transaction_has_valid_signature.return_value = True
    transactions_parser.get_genlayer_transaction.return_value = genlayer_tx

    msg_handler = MagicMock()
    consensus_service = MagicMock()
    consensus_service.generate_transaction_hash.return_value = "0xhash"

    result = endpoints.send_raw_transaction(
        session,
        msg_handler,
        transactions_parser,
        consensus_service,
        signed_rollup_transaction="0xdead",
    )

    assert result == "0xhash"
    assert constructed == ["accounts_manager", "transactions_processor"]
    transactions_processor.insert_transaction.assert_called_once()
    consensus_service.generate_transaction_hash.assert_called_once_with("0xdead")


def test_send_raw_transaction_rejects_foreign_chain_signature(monkeypatch):
    accounts_manager = MagicMock()
    decoded = SimpleNamespace(
        chain_id=1,
        from_address="0x" + "1" * 40,
        value=0,
    )
    transactions_parser = MagicMock()
    transactions_parser.decode_signed_transaction.return_value = decoded
    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: accounts_manager)
    monkeypatch.setattr(
        endpoints, "TransactionsProcessor", lambda _session: MagicMock()
    )
    monkeypatch.setattr(endpoints, "get_simulator_chain_id", lambda: 61127)

    with pytest.raises(endpoints.InvalidTransactionError, match="InvalidChainId"):
        endpoints.send_raw_transaction(
            object(),
            MagicMock(),
            transactions_parser,
            MagicMock(),
            signed_rollup_transaction="0xdead",
        )

    transactions_parser.transaction_has_valid_signature.assert_not_called()


def test_send_raw_transaction_rejects_protocol_calldata_to_wrong_destination(
    monkeypatch,
):
    main = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"
    decoded = DecodedRollupTransaction(
        from_address="0x" + "1" * 40,
        to_address="0x" + "9" * 40,
        data=DecodedRollupTransactionData(
            function_name="addTransaction",
            args=DecodedRollupTransactionDataArgs(
                sender="0x" + "1" * 40,
                recipient="0x" + "2" * 40,
                num_of_initial_validators=5,
                max_rotations=0,
                data="0x",
            ),
        ),
        type="2",
        nonce=0,
        value=0,
        raw_data="0x35a251fb",
    )
    parser = MagicMock()
    parser.decode_signed_transaction.return_value = decoded
    consensus_service = MagicMock()
    consensus_service.public_consensus_main_address.return_value = main
    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: MagicMock())
    monkeypatch.setattr(
        endpoints, "TransactionsProcessor", lambda _session: MagicMock()
    )

    with pytest.raises(
        endpoints.InvalidTransactionError, match="InvalidConsensusDestination"
    ):
        endpoints.send_raw_transaction(
            object(),
            MagicMock(),
            parser,
            consensus_service,
            signed_rollup_transaction="0xdead",
        )


def test_send_raw_transaction_mines_unknown_consensus_selector_as_revert(monkeypatch):
    main = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"
    decoded = DecodedRollupTransaction(
        from_address="0x" + "1" * 40,
        to_address=main,
        data=None,
        type="2",
        nonce=0,
        value=0,
        raw_data="0xdeadbeef",
    )
    parser = MagicMock()
    parser.decode_signed_transaction.return_value = decoded
    parser.transaction_has_valid_signature.return_value = True
    consensus_service = MagicMock()
    consensus_service.public_consensus_main_address.return_value = main
    consensus_service.generate_transaction_hash.return_value = "0x" + "ab" * 32
    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True
    processor = MagicMock()
    processor.begin_evm_envelope.side_effect = [None, None]
    session = MagicMock()
    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: accounts_manager)
    monkeypatch.setattr(endpoints, "TransactionsProcessor", lambda _session: processor)

    result = endpoints.send_raw_transaction(
        session,
        MagicMock(),
        parser,
        consensus_service,
        signed_rollup_transaction="0xdead",
    )

    assert result == "0x" + "ab" * 32
    processor.record_evm_envelope.assert_called_once_with(
        result,
        decoded.from_address,
        0,
        result,
        to_address=main,
        success=False,
        error="UnknownConsensusSelector",
    )


def test_send_raw_transaction_mines_fee_rejection_as_revert(monkeypatch):
    main = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"
    sender = "0x" + "1" * 40
    envelope_hash = "0x" + "ab" * 32
    decoded = DecodedRollupTransaction(
        from_address=sender,
        to_address=main,
        data=DecodedRollupTransactionData(
            function_name="addTransaction",
            args=DecodedRollupTransactionDataArgs(
                sender=sender,
                recipient="0x" + "2" * 40,
                num_of_initial_validators=5,
                max_rotations=0,
                data="0x",
                fees_distribution={},
            ),
        ),
        type="2",
        nonce=0,
        value=0,
        raw_data="0x35a251fb",
    )
    parser = MagicMock()
    parser.decode_signed_transaction.return_value = decoded
    parser.transaction_has_valid_signature.return_value = True
    consensus_service = MagicMock()
    consensus_service.public_consensus_main_address.return_value = main
    consensus_service.generate_transaction_hash.return_value = envelope_hash
    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True
    accounts_manager.get_account.return_value = object()
    processor = MagicMock()
    processor.begin_evm_envelope.side_effect = [None, None]
    session = MagicMock()
    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: accounts_manager)
    monkeypatch.setattr(endpoints, "TransactionsProcessor", lambda _session: processor)

    def reject_fee_envelope(_decoded):
        raise endpoints.InvalidTransactionError("InsufficientFees")

    monkeypatch.setattr(
        endpoints,
        "_validate_fee_envelope",
        reject_fee_envelope,
    )

    result = endpoints.send_raw_transaction(
        session,
        MagicMock(),
        parser,
        consensus_service,
        signed_rollup_transaction="0xdead",
    )

    assert result == envelope_hash
    parser.get_genlayer_transaction.assert_not_called()
    assert processor.begin_evm_envelope.call_count == 2
    processor.record_evm_envelope.assert_called_once_with(
        envelope_hash,
        sender,
        0,
        envelope_hash,
        to_address=main,
        success=False,
        error="InsufficientFees",
    )
    session.rollback.assert_called_once()
    session.commit.assert_called_once()


def test_send_raw_lifecycle_call_returns_signed_envelope_hash(monkeypatch):
    main = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"
    sender = "0x" + "1" * 40
    envelope_hash = "0x" + "ab" * 32
    target_tx_id = "0x" + "cd" * 32
    decoded = DecodedRollupTransaction(
        from_address=sender,
        to_address=main,
        data=DecodedTopUpFeesDataArgs(
            tx_id=target_tx_id,
            fees_distribution={},
        ),
        type="2",
        nonce=0,
        value=1,
        fee_value=1,
        raw_data="0xdeadbeef",
    )
    session = MagicMock()
    accounts_manager = MagicMock()
    accounts_manager.get_account.return_value = object()
    transactions_processor = MagicMock()
    transactions_processor.begin_evm_envelope.return_value = None
    parser = MagicMock()
    parser.decode_signed_transaction.return_value = decoded
    parser.transaction_has_valid_signature.return_value = True
    consensus_service = MagicMock()
    consensus_service.public_consensus_main_address.return_value = main
    consensus_service.generate_transaction_hash.return_value = envelope_hash
    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: accounts_manager)
    monkeypatch.setattr(
        endpoints, "TransactionsProcessor", lambda _session: transactions_processor
    )
    handler = MagicMock(return_value=target_tx_id)
    monkeypatch.setattr(endpoints, "_handle_top_up_fees", handler)

    result = endpoints.send_raw_transaction(
        session,
        MagicMock(),
        parser,
        consensus_service,
        signed_rollup_transaction="0xdead",
    )

    assert result == envelope_hash
    handler.assert_called_once()
    transactions_processor.record_evm_envelope.assert_called_once_with(
        envelope_hash,
        sender,
        0,
        envelope_hash,
        to_address=main,
    )
    session.commit.assert_called_once()


def test_send_raw_appeal_publishes_event_only_after_envelope_commit(monkeypatch):
    main = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"
    sender = "0x" + "1" * 40
    envelope_hash = "0x" + "ab" * 32
    target_tx_id = "0x" + "cd" * 32
    decoded = DecodedRollupTransaction(
        from_address=sender,
        to_address=main,
        data=DecodedsubmitAppealDataArgs(
            tx_id=target_tx_id,
            expected_decision_id=1,
        ),
        type="2",
        nonce=0,
        value=1,
        raw_data="0xdeadbeef",
    )
    session = MagicMock()
    accounts_manager = MagicMock()
    accounts_manager.get_account.return_value = object()
    transactions_processor = MagicMock()
    transactions_processor.begin_evm_envelope.return_value = None
    parser = MagicMock()
    parser.decode_signed_transaction.return_value = decoded
    parser.transaction_has_valid_signature.return_value = True
    consensus_service = MagicMock()
    consensus_service.public_consensus_main_address.return_value = main
    consensus_service.generate_transaction_hash.return_value = envelope_hash
    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: accounts_manager)
    monkeypatch.setattr(
        endpoints, "TransactionsProcessor", lambda _session: transactions_processor
    )
    handler = MagicMock(return_value=target_tx_id)
    monkeypatch.setattr(endpoints, "_handle_appeal_or_top_up_and_submit", handler)
    publish_commit_states = []
    msg_handler = MagicMock()
    msg_handler.send_message.side_effect = (
        lambda **_kwargs: publish_commit_states.append(session.commit.called)
    )

    result = endpoints.send_raw_transaction(
        session,
        msg_handler,
        parser,
        consensus_service,
        signed_rollup_transaction="0xdead",
    )

    assert result == envelope_hash
    assert handler.call_args.kwargs["emit_event"] is False
    assert publish_commit_states == [True]
    session.commit.assert_called_once()


def test_reverted_raw_lifecycle_call_consumes_nonce_and_returns_envelope_hash(
    monkeypatch,
):
    main = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"
    sender = "0x" + "1" * 40
    envelope_hash = "0x" + "ab" * 32
    decoded = DecodedRollupTransaction(
        from_address=sender,
        to_address=main,
        data=DecodedFinalizeTransactionDataArgs(
            tx_id="0x" + "cd" * 32,
            expected_decision_id=1,
        ),
        type="2",
        nonce=0,
        value=1,
        raw_data="0xdeadbeef",
    )
    session = MagicMock()
    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True
    accounts_manager.get_account.return_value = object()
    transactions_processor = MagicMock()
    transactions_processor.begin_evm_envelope.side_effect = [None, None]
    parser = MagicMock()
    parser.decode_signed_transaction.return_value = decoded
    parser.transaction_has_valid_signature.return_value = True
    consensus_service = MagicMock()
    consensus_service.public_consensus_main_address.return_value = main
    consensus_service.generate_transaction_hash.return_value = envelope_hash
    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: accounts_manager)
    monkeypatch.setattr(
        endpoints, "TransactionsProcessor", lambda _session: transactions_processor
    )

    result = endpoints.send_raw_transaction(
        session,
        MagicMock(),
        parser,
        consensus_service,
        signed_rollup_transaction="0xdead",
    )

    assert result == envelope_hash
    assert transactions_processor.begin_evm_envelope.call_count == 2
    transactions_processor.record_evm_envelope.assert_called_once_with(
        envelope_hash,
        sender,
        0,
        envelope_hash,
        to_address=main,
        success=False,
        error="NonPayableCall",
    )
    session.rollback.assert_called_once()
    session.commit.assert_called_once()


def test_send_raw_transaction_rejects_missing_contract_before_shadow_mutation(
    monkeypatch,
):
    session = object()
    sender = "0x" + "1" * 40
    recipient = "0x" + "2" * 40
    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True
    accounts_manager.get_account.return_value = object()
    transactions_processor = MagicMock()
    transactions_processor.get_transaction_by_hash.return_value = None
    transactions_processor.begin_evm_envelope.return_value = None

    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: accounts_manager)
    monkeypatch.setattr(
        endpoints,
        "TransactionsProcessor",
        lambda _session: transactions_processor,
    )
    monkeypatch.setattr(endpoints, "live_state_column_size", lambda *_args: None)

    decoded = SimpleNamespace(
        from_address=sender,
        value=0,
        total_spend=0,
        data=object(),
        raw_data="0x35a251fb",
        to_address=recipient,
        nonce=1,
    )
    genlayer_tx = SimpleNamespace(
        type=TransactionType.RUN_CONTRACT,
        from_address=sender,
        to_address=recipient,
        max_rotations=0,
        num_of_initial_validators=5,
        data=SimpleNamespace(
            leader_only=False,
            execution_mode="NORMAL",
            calldata=b"payload",
        ),
    )
    transactions_parser = MagicMock()
    transactions_parser.decode_signed_transaction.return_value = decoded
    transactions_parser.transaction_has_valid_signature.return_value = True
    transactions_parser.get_genlayer_transaction.return_value = genlayer_tx
    consensus_service = MagicMock()
    consensus_service.generate_transaction_hash.return_value = "0xhash"

    with pytest.raises(endpoints.NotFoundError, match="Contract not found"):
        endpoints.send_raw_transaction(
            session,
            MagicMock(),
            transactions_parser,
            consensus_service,
            signed_rollup_transaction="0xdead",
        )

    consensus_service.add_transaction.assert_not_called()
    transactions_processor.insert_transaction.assert_not_called()


def test_send_raw_transaction_uses_recovered_signer_without_shadow_mutation(
    monkeypatch,
):
    session = object()
    signer = "0x" + "1" * 40
    claimed_sender = "0x" + "9" * 40
    recipient = "0x" + "2" * 40
    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True
    accounts_manager.get_account.return_value = object()
    transactions_processor = MagicMock()
    transactions_processor.get_transaction_by_hash.return_value = None
    transactions_processor.get_transaction_status.return_value = "PENDING"
    transactions_processor.begin_evm_envelope.return_value = None

    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: accounts_manager)
    monkeypatch.setattr(
        endpoints,
        "TransactionsProcessor",
        lambda _session: transactions_processor,
    )
    monkeypatch.setattr(endpoints, "live_state_column_size", lambda *_args: 1)
    monkeypatch.setattr(
        endpoints, "_enforce_pending_queue_caps", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        endpoints,
        "enforce_contract_storage_quota",
        lambda *_args: None,
    )

    decoded = SimpleNamespace(
        from_address=signer,
        value=0,
        total_spend=0,
        data=object(),
        raw_data="0x35a251fb",
        to_address=recipient,
        nonce=1,
    )
    genlayer_tx = SimpleNamespace(
        type=TransactionType.RUN_CONTRACT,
        from_address=claimed_sender,
        to_address=recipient,
        max_rotations=0,
        num_of_initial_validators=5,
        data=SimpleNamespace(
            leader_only=False,
            execution_mode="NORMAL",
            calldata=b"payload",
        ),
    )
    transactions_parser = MagicMock()
    transactions_parser.decode_signed_transaction.return_value = decoded
    transactions_parser.transaction_has_valid_signature.return_value = True
    transactions_parser.get_genlayer_transaction.return_value = genlayer_tx
    consensus_service = MagicMock()
    consensus_service.generate_transaction_hash.return_value = "0xhash"

    result = endpoints.send_raw_transaction(
        session,
        MagicMock(),
        transactions_parser,
        consensus_service,
        signed_rollup_transaction="0xdead",
    )

    assert result == "0xhash"
    assert genlayer_tx.from_address == signer
    consensus_service.add_transaction.assert_not_called()
    assert transactions_processor.insert_transaction.call_args.args[0] == signer


def test_update_transaction_status_uses_request_session(monkeypatch):
    session = object()
    transactions_processor = MagicMock()
    transactions_processor.get_transaction_by_hash.return_value = {
        "hash": "0x" + "a" * 64
    }

    created = []

    def transactions_processor_factory(s):
        assert s is session
        created.append(s)
        return transactions_processor

    monkeypatch.setattr(
        endpoints, "TransactionsProcessor", transactions_processor_factory
    )

    tx_hash = "0x" + "a" * 64
    result = endpoints.update_transaction_status(
        session,
        tx_hash,
        TransactionStatus.FINALIZED.value,
    )

    assert result == {"hash": tx_hash}
    assert created == [session]
    transactions_processor.update_transaction_status.assert_called_once_with(
        transaction_hash=tx_hash,
        new_status=TransactionStatus.FINALIZED,
        update_current_status_changes=True,
    )
