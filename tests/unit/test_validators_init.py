import pytest
from unittest.mock import Mock
from backend.protocol_rpc.validators_init import initialize_validators


def test_initialize_validators_empty_json():
    """Test that empty JSON string returns without doing anything"""
    mock_registry = Mock()
    mock_accounts = Mock()
    mock_creator = Mock()

    initialize_validators("", mock_registry, mock_accounts, mock_creator)

    mock_registry.delete_all_validators.assert_not_called()
    mock_creator.assert_not_called()


def test_initialize_validators_invalid_json():
    """Test that invalid JSON raises ValueError"""
    mock_registry = Mock()
    mock_accounts = Mock()
    mock_creator = Mock()

    with pytest.raises(ValueError, match="Invalid JSON"):
        initialize_validators(
            "{invalid json", mock_registry, mock_accounts, mock_creator
        )


def test_initialize_validators_non_array_json():
    """Test that non-array JSON raises ValueError"""
    mock_registry = Mock()
    mock_accounts = Mock()
    mock_creator = Mock()

    with pytest.raises(ValueError, match="must contain a JSON array"):
        initialize_validators("{}", mock_registry, mock_accounts, mock_creator)


def test_initialize_validators_success():
    """Test successful initialization of validators"""
    mock_registry = Mock()
    mock_accounts = Mock()
    mock_creator = Mock()

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

    initialize_validators(validators_json, mock_registry, mock_accounts, mock_creator)

    # Verify that existing validators were deleted
    mock_registry.delete_all_validators.assert_called_once()

    # Verify that creator was called for each validator with correct arguments
    assert mock_creator.call_count == 3

    # Check first validator creation call
    mock_creator.assert_any_call(
        mock_registry,
        mock_accounts,
        100,
        "test-provider",
        "test-model",
        {"key": "value"},
        "test-plugin",
        {"plugin_key": "plugin_value"},
    )

    # Check second validator creation call
    mock_creator.assert_any_call(
        mock_registry,
        mock_accounts,
        200,
        "another-provider",
        "another-model",
        None,
        None,
        None,
    )

    mock_creator.assert_any_call(
        mock_registry,
        mock_accounts,
        200,
        "another-provider",
        "another-model",
        None,
        None,
        None,
    )


def test_initialize_validators_invalid_config():
    """Test that invalid validator configuration raises ValueError"""
    mock_registry = Mock()
    mock_accounts = Mock()
    mock_creator = Mock()

    # Missing required field 'model'
    validators_json = """[
        {
            "stake": 100,
            "provider": "test-provider"
        }
    ]"""

    with pytest.raises(ValueError, match="Failed to create validator"):
        initialize_validators(
            validators_json, mock_registry, mock_accounts, mock_creator
        )


def test_initialize_validators_creator_error():
    """Test that creator function errors are properly handled"""
    mock_registry = Mock()
    mock_accounts = Mock()
    mock_creator = Mock(side_effect=Exception("Creator error"))

    validators_json = """[
        {
            "stake": 100,
            "provider": "test-provider",
            "model": "test-model"
        }
    ]"""

    with pytest.raises(ValueError, match="Failed to create validator.*Creator error"):
        initialize_validators(
            validators_json, mock_registry, mock_accounts, mock_creator
        )
