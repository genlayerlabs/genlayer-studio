import pytest
from unittest.mock import Mock, AsyncMock, patch
from backend.protocol_rpc.validators_init import (
    initialize_validators,
    _current_config_hash,
    _desired_config_hash,
)
from types import SimpleNamespace


@pytest.mark.asyncio
async def test_initialize_validators_empty_json():
    """Test that empty JSON string returns without doing anything"""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
    validators_manager = SimpleNamespace(registry=mock_registry)

    await initialize_validators("", mock_db_session, validators_manager)

    mock_registry.delete_all_validators.assert_not_called()
    mock_registry.batch_create_validators.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_validators_skips_when_config_unchanged():
    """If DB validators match desired config, skip reinitialization."""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
    # Return validators that match the desired config
    mock_registry.get_all_validators = Mock(
        return_value=[
            {"provider": "p", "model": "m", "stake": 100},
        ]
    )
    validators_manager = SimpleNamespace(registry=mock_registry)

    validators_json = '[{"stake": 100, "provider": "p", "model": "m"}]'

    await initialize_validators(validators_json, mock_db_session, validators_manager)

    mock_registry.delete_all_validators.assert_not_called()
    mock_registry.batch_create_validators.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_validators_reinitializes_when_config_changed():
    """If DB validators differ from desired config, reinitialize."""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
    mock_registry.batch_create_validators = AsyncMock()
    # DB has old config
    mock_registry.get_all_validators = Mock(
        return_value=[
            {"provider": "old-provider", "model": "old-model", "stake": 50},
        ]
    )
    validators_manager = SimpleNamespace(registry=mock_registry)

    validators_json = (
        '[{"stake": 100, "provider": "new-provider", "model": "new-model"}]'
    )

    with patch(
        "backend.database_handler.accounts_manager.AccountsManager"
    ) as mock_am_class:
        with patch(
            "backend.node.create_nodes.providers.get_default_provider_for"
        ) as mock_get_provider:
            mock_am = Mock()
            mock_am.create_new_account.return_value = SimpleNamespace(
                address="0xtest", key="privkey"
            )
            mock_am_class.return_value = mock_am

            from backend.domain.types import LLMProvider

            mock_get_provider.return_value = LLMProvider(
                provider="new-provider",
                model="new-model",
                config={},
                plugin="",
                plugin_config={},
            )

            await initialize_validators(
                validators_json, mock_db_session, validators_manager
            )

            mock_registry.delete_all_validators.assert_called_once()
            mock_registry.batch_create_validators.assert_called_once()


@pytest.mark.asyncio
async def test_initialize_validators_invalid_json():
    """Test that invalid JSON raises ValueError"""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
    validators_manager = SimpleNamespace(registry=mock_registry)

    with pytest.raises(ValueError, match="Invalid JSON"):
        await initialize_validators(
            "{invalid json", mock_db_session, validators_manager
        )


@pytest.mark.asyncio
async def test_initialize_validators_non_array_json():
    """Test that non-array JSON raises ValueError"""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
    validators_manager = SimpleNamespace(registry=mock_registry)

    with pytest.raises(ValueError, match="must contain a JSON array"):
        await initialize_validators("{}", mock_db_session, validators_manager)


@pytest.mark.asyncio
async def test_initialize_validators_success():
    """Test successful initialization when DB is empty"""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
    mock_registry.batch_create_validators = AsyncMock()
    # Empty DB → get_all_validators returns empty list
    mock_registry.get_all_validators = Mock(return_value=[])
    validators_manager = SimpleNamespace(registry=mock_registry)

    validators_json = """[
        {
            "stake": 100,
            "provider": "test-provider",
            "model": "test-model",
            "config": {"key": "value"},
            "plugin": "test-plugin",
            "plugin_config": {"plugin_key": "plugin_value"}
        },
        {
            "stake": 200,
            "provider": "another-provider",
            "model": "another-model",
            "amount": 2
        }
    ]"""

    with patch(
        "backend.database_handler.accounts_manager.AccountsManager"
    ) as mock_accounts_manager_class:
        with patch(
            "backend.node.create_nodes.providers.get_default_provider_for"
        ) as mock_get_provider:
            mock_accounts_manager = Mock()
            mock_account = SimpleNamespace(address="0xtest", key="privkey")
            mock_accounts_manager.create_new_account.return_value = mock_account
            mock_accounts_manager_class.return_value = mock_accounts_manager

            from backend.domain.types import LLMProvider

            mock_get_provider.return_value = LLMProvider(
                provider="test-provider",
                model="test-model",
                config={},
                plugin="test-plugin",
                plugin_config={},
            )

            await initialize_validators(
                validators_json, mock_db_session, validators_manager
            )

            mock_registry.delete_all_validators.assert_called_once()
            mock_registry.batch_create_validators.assert_called_once()
            validators_arg = mock_registry.batch_create_validators.call_args[0][0]
            assert len(validators_arg) == 3
            mock_accounts_manager_class.assert_called_once_with(mock_db_session)
            assert mock_accounts_manager.create_new_account.call_count == 3


@pytest.mark.asyncio
async def test_initialize_validators_invalid_config():
    """Test that invalid validator configuration raises ValueError"""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
    mock_registry.get_all_validators = Mock(return_value=[])
    validators_manager = SimpleNamespace(registry=mock_registry)

    # Missing required field 'model'
    validators_json = """[
        {
            "stake": 100,
            "provider": "test-provider"
        }
    ]"""

    with pytest.raises(ValueError, match="Failed to create validator"):
        await initialize_validators(
            validators_json, mock_db_session, validators_manager
        )


@pytest.mark.asyncio
async def test_initialize_validators_batch_create_error():
    """Test that batch_create_validators errors are properly propagated"""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
    mock_registry.batch_create_validators = AsyncMock(
        side_effect=Exception("Batch create error")
    )
    mock_registry.get_all_validators = Mock(return_value=[])
    validators_manager = SimpleNamespace(registry=mock_registry)

    validators_json = """[
        {
            "stake": 100,
            "provider": "test-provider",
            "model": "test-model"
        }
    ]"""

    with patch(
        "backend.database_handler.accounts_manager.AccountsManager"
    ) as mock_accounts_manager_class:
        with patch(
            "backend.node.create_nodes.providers.get_default_provider_for"
        ) as mock_get_provider:
            mock_accounts_manager = Mock()
            mock_account = SimpleNamespace(address="0xtest", key="privkey")
            mock_accounts_manager.create_new_account.return_value = mock_account
            mock_accounts_manager_class.return_value = mock_accounts_manager

            from backend.domain.types import LLMProvider

            mock_get_provider.return_value = LLMProvider(
                provider="test-provider",
                model="test-model",
                config={},
                plugin="test-plugin",
                plugin_config={},
            )

            with pytest.raises(Exception, match="Batch create error"):
                await initialize_validators(
                    validators_json, mock_db_session, validators_manager
                )


def test_config_hash_consistency():
    """Verify desired and current hashes match for equivalent configs."""
    validators_json = '[{"stake": 100, "provider": "p", "model": "m", "amount": 2}]'

    # Simulate DB having 2 validators with same provider/model/stake
    mock_registry = Mock()
    mock_registry.get_all_validators = Mock(
        return_value=[
            {"provider": "p", "model": "m", "stake": 100},
            {"provider": "p", "model": "m", "stake": 100},
        ]
    )

    desired = _desired_config_hash(validators_json)
    current = _current_config_hash(mock_registry)
    assert desired == current


def test_config_hash_differs_on_change():
    """Hashes must differ when config changes."""
    mock_registry = Mock()
    mock_registry.get_all_validators = Mock(
        return_value=[
            {"provider": "old", "model": "m", "stake": 100},
        ]
    )

    desired = _desired_config_hash('[{"stake": 100, "provider": "new", "model": "m"}]')
    current = _current_config_hash(mock_registry)
    assert desired != current


def test_current_config_hash_empty_db():
    """Empty DB returns None."""
    mock_registry = Mock()
    mock_registry.get_all_validators = Mock(return_value=[])
    assert _current_config_hash(mock_registry) is None
