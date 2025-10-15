import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from backend.protocol_rpc.validators_init import initialize_validators


@pytest.mark.asyncio
async def test_initialize_validators_empty_json():
    """Test that empty JSON string returns without doing anything"""
    mock_db_session = Mock()
    mock_validators_manager = Mock()
    mock_registry = AsyncMock()
    mock_validators_manager.registry = mock_registry

    await initialize_validators("", mock_db_session, mock_validators_manager)

    mock_registry.delete_all_validators.assert_not_called()
    mock_registry.create_validator.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_validators_invalid_json():
    """Test that invalid JSON raises ValueError"""
    mock_db_session = Mock()
    mock_validators_manager = Mock()

    with pytest.raises(ValueError, match="Invalid JSON"):
        await initialize_validators(
            "{invalid json", mock_db_session, mock_validators_manager
        )


@pytest.mark.asyncio
async def test_initialize_validators_non_array_json():
    """Test that non-array JSON raises ValueError"""
    mock_db_session = Mock()
    mock_validators_manager = Mock()

    with pytest.raises(ValueError, match="must contain a JSON array"):
        await initialize_validators("{}", mock_db_session, mock_validators_manager)


@pytest.mark.asyncio
async def test_initialize_validators_success():
    """Test successful initialization of validators"""
    mock_db_session = Mock()
    mock_validators_manager = Mock()
    mock_registry = AsyncMock()
    mock_validators_manager.registry = mock_registry

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
    ) as mock_accounts_manager_class, patch(
        "backend.node.create_nodes.providers.get_default_provider_for"
    ) as mock_get_default_provider:
        # Setup mock account creation
        mock_accounts_manager = Mock()
        mock_accounts_manager_class.return_value = mock_accounts_manager

        mock_account = Mock()
        mock_account.address = "0x123"
        mock_account.key = "private_key"
        mock_accounts_manager.create_new_account.return_value = mock_account

        # Setup mock provider
        mock_provider = Mock()
        mock_get_default_provider.return_value = mock_provider

        await initialize_validators(
            validators_json, mock_db_session, mock_validators_manager
        )

        # Verify that existing validators were deleted
        mock_registry.delete_all_validators.assert_called_once()

        # Verify that create_validator was called 3 times (1 + 2)
        assert mock_registry.create_validator.call_count == 3

        # Verify accounts were created for each validator
        assert mock_accounts_manager.create_new_account.call_count == 3


@pytest.mark.asyncio
async def test_initialize_validators_invalid_config():
    """Test that invalid validator configuration raises ValueError"""
    mock_db_session = Mock()
    mock_validators_manager = Mock()
    mock_registry = AsyncMock()
    mock_validators_manager.registry = mock_registry

    # Missing required field 'model'
    validators_json = """[
        {
            "stake": 100,
            "provider": "test-provider"
        }
    ]"""

    with pytest.raises(ValueError, match="Failed to create validator"):
        await initialize_validators(
            validators_json, mock_db_session, mock_validators_manager
        )


@pytest.mark.asyncio
async def test_initialize_validators_creator_error():
    """Test that account creation errors are properly handled"""
    mock_db_session = Mock()
    mock_validators_manager = Mock()
    mock_registry = AsyncMock()
    mock_validators_manager.registry = mock_registry

    validators_json = """[
        {
            "stake": 100,
            "provider": "test-provider",
            "model": "test-model"
        }
    ]"""

    with patch(
        "backend.database_handler.accounts_manager.AccountsManager"
    ) as mock_accounts_manager_class, patch(
        "backend.node.create_nodes.providers.get_default_provider_for"
    ) as mock_get_default_provider:
        # Setup mock provider
        mock_provider = Mock()
        mock_get_default_provider.return_value = mock_provider

        # Setup mock to raise an error
        mock_accounts_manager = Mock()
        mock_accounts_manager_class.return_value = mock_accounts_manager
        mock_accounts_manager.create_new_account.side_effect = Exception(
            "Account creation error"
        )

        with pytest.raises(
            ValueError, match="Failed to create validator.*Account creation error"
        ):
            await initialize_validators(
                validators_json, mock_db_session, mock_validators_manager
            )
