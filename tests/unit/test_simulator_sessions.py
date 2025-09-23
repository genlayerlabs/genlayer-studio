from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.protocol_rpc import endpoints
from backend.errors.errors import InvalidAddressError
from backend.domain.types import LLMProvider, TransactionType
from backend.database_handler.models import TransactionStatus


def test_fund_account_uses_request_scoped_session(monkeypatch):
    session = object()
    accounts_manager_instance = MagicMock()
    accounts_manager_instance.is_valid_address.return_value = True

    transactions_processor_instance = MagicMock()
    transactions_processor_instance.get_transaction_count.return_value = 12
    transactions_processor_instance.insert_transaction.return_value = "0xabc"

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
        None, "0x" + "1" * 40, None, 25, 0, 12, False, 0
    )


def test_fund_account_raises_for_invalid_address(monkeypatch):
    session = object()
    accounts_manager_instance = MagicMock()
    accounts_manager_instance.is_valid_address.return_value = False

    monkeypatch.setattr(
        endpoints, "AccountsManager", lambda s: accounts_manager_instance
    )
    monkeypatch.setattr(endpoints, "TransactionsProcessor", lambda s: MagicMock())

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

    def fake_transactions_processor(session):
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

    monkeypatch.setattr(endpoints, "validate_provider", lambda provider: None)
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

    monkeypatch.setattr(endpoints, "validate_provider", lambda provider: None)
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

    monkeypatch.setattr(endpoints, "validate_provider", lambda provider: None)
    monkeypatch.setattr(
        endpoints,
        "AccountsManager",
        lambda s: accounts_manager_instance if s is session else None,
    )
    monkeypatch.setattr(
        endpoints,
        "ModifiableValidatorsRegistry",
        lambda s: registry_instance if s is session else None,
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

    def registry_factory(s):
        assert s is session
        return registry_instance

    monkeypatch.setattr(endpoints, "AccountsManager", accounts_manager_factory)
    monkeypatch.setattr(endpoints, "ModifiableValidatorsRegistry", registry_factory)
    monkeypatch.setattr(endpoints, "LLMProviderRegistry", FakeLLMRegistry)
    monkeypatch.setattr(
        endpoints, "random_validator_config", fake_random_validator_config
    )

    validators_manager = MagicMock()

    response = await endpoints.create_random_validators(
        session,
        validators_manager,
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

    monkeypatch.setattr(endpoints, "validate_provider", lambda provider: None)

    def update_registry_factory(s):
        assert s is session
        return registry_instance

    monkeypatch.setattr(
        endpoints, "ModifiableValidatorsRegistry", update_registry_factory
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

    result = await endpoints.update_validator(
        session,
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
    session = object()
    registry_instance = SimpleNamespace(delete_validator=AsyncMock())

    def delete_registry_factory(s):
        assert s is session
        return registry_instance

    monkeypatch.setattr(
        endpoints, "ModifiableValidatorsRegistry", delete_registry_factory
    )

    result = await endpoints.delete_validator(session, "0xabc")

    assert result == "0xabc"
    registry_instance.delete_validator.assert_awaited_once_with("0xabc")


@pytest.mark.asyncio
async def test_delete_all_validators_uses_request_session(monkeypatch):
    session = object()
    registry_instance = SimpleNamespace(
        delete_all_validators=AsyncMock(),
        get_all_validators=MagicMock(return_value=[]),
    )

    def delete_all_registry_factory(s):
        assert s is session
        return registry_instance

    monkeypatch.setattr(
        endpoints, "ModifiableValidatorsRegistry", delete_all_registry_factory
    )

    result = await endpoints.delete_all_validators(session)

    assert result == []
    registry_instance.delete_all_validators.assert_awaited_once()
    registry_instance.get_all_validators.assert_called_once()


def test_send_raw_transaction_uses_request_session(monkeypatch):
    session = object()
    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True
    transactions_processor = MagicMock()
    transactions_processor.insert_transaction.return_value = "0xhash"

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
