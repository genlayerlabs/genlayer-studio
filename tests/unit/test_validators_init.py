import pytest
from unittest.mock import Mock, AsyncMock, patch
from backend.protocol_rpc.validators_init import initialize_validators
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
    """Test successful initialization of validators"""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
    mock_registry.batch_create_validators = AsyncMock()
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
            # Mock accounts manager
            mock_accounts_manager = Mock()
            mock_account = SimpleNamespace(address="0xtest", key="privkey")
            mock_accounts_manager.create_new_account.return_value = mock_account
            mock_accounts_manager_class.return_value = mock_accounts_manager

            # Mock provider
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

            # Verify that existing validators were deleted
            mock_registry.delete_all_validators.assert_called_once()

            # Verify that batch_create_validators was called once with 3 validators
            mock_registry.batch_create_validators.assert_called_once()
            validators_arg = mock_registry.batch_create_validators.call_args[0][0]
            assert len(validators_arg) == 3

            # Verify AccountsManager was created with correct session
            mock_accounts_manager_class.assert_called_once_with(mock_db_session)

            # Verify accounts were created
            assert mock_accounts_manager.create_new_account.call_count == 3


@pytest.mark.asyncio
async def test_initialize_validators_invalid_config():
    """Test that invalid validator configuration raises ValueError"""
    mock_db_session = Mock()
    mock_registry = AsyncMock()
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
            # Mock accounts manager
            mock_accounts_manager = Mock()
            mock_account = SimpleNamespace(address="0xtest", key="privkey")
            mock_accounts_manager.create_new_account.return_value = mock_account
            mock_accounts_manager_class.return_value = mock_accounts_manager

            # Mock provider
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
