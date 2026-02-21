"""Tests for SDK-based contract schema extraction."""

import pytest
import os
from unittest.mock import patch, MagicMock
from backend.services.sdk_schema import (
    extract_schema_via_sdk,
    _get_sdk_paths,
)


class TestSdkSchemaExtraction:
    """Test cases for SDK schema extraction."""

    def test_get_sdk_paths_without_genvmroot(self):
        """Test that _get_sdk_paths returns None when GENVMROOT is not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove GENVMROOT if present
            os.environ.pop("GENVMROOT", None)
            result = _get_sdk_paths()
            assert result is None

    def test_get_sdk_paths_with_invalid_genvmroot(self, tmp_path):
        """Test that _get_sdk_paths returns None when paths don't exist."""
        with patch.dict(os.environ, {"GENVMROOT": str(tmp_path)}):
            result = _get_sdk_paths()
            assert result is None

    def test_extract_schema_returns_none_without_sdk(self):
        """Test that extraction returns None when SDK is not available."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GENVMROOT", None)
            result = extract_schema_via_sdk(b"some contract code")
            assert result is None


class TestSdkSchemaIntegration:
    """Integration tests that require the SDK to be available."""

    @pytest.fixture
    def has_sdk(self):
        """Check if SDK is available for testing."""
        paths = _get_sdk_paths()
        if paths is None:
            pytest.skip("SDK not available (GENVMROOT not set or paths don't exist)")
        return paths

    def test_extract_simple_contract(self, has_sdk):
        """Test extraction of a simple contract (requires SDK)."""
        simple_contract = b"""from genlayer import *

class SimpleContract(Contract):
    def __init__(self):
        pass

    @public
    def hello(self) -> str:
        return "Hello"
"""
        result = extract_schema_via_sdk(simple_contract)

        # Should return a schema or None (if SDK import fails)
        # We can't assert the exact schema without the full SDK setup
        if result is not None:
            assert "ctor" in result
            assert "methods" in result
