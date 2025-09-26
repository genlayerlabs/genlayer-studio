import pytest
from unittest.mock import Mock, AsyncMock, patch
from backend.protocol_rpc.validators_init import initialize_validators


@pytest.mark.asyncio
async def test_initialize_validators_empty_json():
    """Test that empty JSON string returns without doing anything"""
    mock_db_session = Mock()
    mock_creator = AsyncMock()

    with patch(
        "backend.protocol_rpc.validators_init.ModifiableValidatorsRegistry"
    ) as mock_registry_class:
        mock_registry = AsyncMock()
        mock_registry_class.return_value = mock_registry

        await initialize_validators("", mock_db_session, mock_creator)

        mock_registry_class.assert_not_called()
        mock_creator.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_validators_invalid_json():
    """Test that invalid JSON raises ValueError"""
    mock_db_session = Mock()
    mock_creator = AsyncMock()

    with pytest.raises(ValueError, match="Invalid JSON"):
        await initialize_validators("{invalid json", mock_db_session, mock_creator)


@pytest.mark.asyncio
async def test_initialize_validators_non_array_json():
    """Test that non-array JSON raises ValueError"""
    mock_db_session = Mock()
    mock_creator = AsyncMock()

    with pytest.raises(ValueError, match="must contain a JSON array"):
        await initialize_validators("{}", mock_db_session, mock_creator)


@pytest.mark.asyncio
async def test_initialize_validators_success():
    """Test successful initialization of validators"""
    mock_db_session = Mock()
    mock_creator = AsyncMock()

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
        "backend.protocol_rpc.validators_init.ModifiableValidatorsRegistry"
    ) as mock_registry_class:
        mock_registry = AsyncMock()
        mock_registry_class.return_value = mock_registry

        await initialize_validators(validators_json, mock_db_session, mock_creator)

        # Verify that ModifiableValidatorsRegistry was created with db_session
        mock_registry_class.assert_called_once_with(mock_db_session)

        # Verify that existing validators were deleted
        mock_registry.delete_all_validators.assert_called_once()

        # Verify that creator was called for each validator with correct arguments
        assert mock_creator.call_count == 3

        # Check first validator creation call
        mock_creator.assert_any_call(
            mock_db_session,
            100,
            "test-provider",
            "test-model",
            {"key": "value"},
            "test-plugin",
            {"plugin_key": "plugin_value"},
        )

        # Check second and third validator creation calls (amount=2)
        mock_creator.assert_any_call(
            mock_db_session,
            200,
            "another-provider",
            "another-model",
            None,
            None,
            None,
        )


@pytest.mark.asyncio
async def test_initialize_validators_invalid_config():
    """Test that invalid validator configuration raises ValueError"""
    mock_db_session = Mock()
    mock_creator = AsyncMock()

    # Missing required field 'model'
    validators_json = """[
        {
            "stake": 100,
            "provider": "test-provider"
        }
    ]"""

    with patch(
        "backend.protocol_rpc.validators_init.ModifiableValidatorsRegistry"
    ) as mock_registry_class:
        mock_registry = AsyncMock()
        mock_registry_class.return_value = mock_registry

        with pytest.raises(ValueError, match="Failed to create validator"):
            await initialize_validators(validators_json, mock_db_session, mock_creator)


@pytest.mark.asyncio
async def test_initialize_validators_creator_error():
    """Test that creator function errors are properly handled"""
    mock_db_session = Mock()
    mock_creator = AsyncMock(side_effect=Exception("Creator error"))

    validators_json = """[
        {
            "stake": 100,
            "provider": "test-provider",
            "model": "test-model"
        }
    ]"""

    with patch(
        "backend.protocol_rpc.validators_init.ModifiableValidatorsRegistry"
    ) as mock_registry_class:
        mock_registry = AsyncMock()
        mock_registry_class.return_value = mock_registry

        with pytest.raises(
            ValueError, match="Failed to create validator.*Creator error"
        ):
            await initialize_validators(validators_json, mock_db_session, mock_creator)
