import pytest
import json
from unittest.mock import Mock, AsyncMock, patch
from backend.protocol_rpc.endpoints import get_contract_schema_for_code
from backend.protocol_rpc.message_handler.base import MessageHandler


class TestGetContractSchemaForCodeEndpoint:
    """Test cases for the get_contract_schema_for_code RPC endpoint."""

    def setup_method(self):
        """Set up test fixtures."""
        self.msg_handler = Mock(spec=MessageHandler)
        self.msg_handler.with_client_session = Mock(return_value=self.msg_handler)

        # Sample contract code in different formats
        self.simple_contract = """from genlayer import *

@gl_class
class SimpleContract:
    def __init__(self):
        self.value = 0

    @gl_public_write
    def set_value(self, val: int):
        self.value = val

    @gl_public_view
    def get_value(self) -> int:
        return self.value
"""

        # Mock schema response
        self.mock_schema = {
            "class_name": "SimpleContract",
            "methods": [
                {
                    "name": "set_value",
                    "type": "write",
                    "args": [{"name": "val", "type": "int"}],
                },
                {
                    "name": "get_value",
                    "type": "view",
                    "returns": "int",
                },
            ],
        }

        # Patch the Node class
        self.node_patch = patch("backend.protocol_rpc.endpoints.Node")
        self.mock_node_class = self.node_patch.start()
        self.mock_node_instance = Mock()
        self.mock_node_instance.get_contract_schema = AsyncMock(
            return_value=json.dumps(self.mock_schema)
        )
        self.mock_node_class.return_value = self.mock_node_instance

    def teardown_method(self):
        """Clean up patches."""
        self.node_patch.stop()

    @pytest.mark.asyncio
    async def test_valid_hex_encoded_contract_code(self):
        """Test successful schema retrieval with hex-encoded contract code."""
        # Encode contract as hex (with 0x prefix)
        contract_bytes = self.simple_contract.encode("utf-8")
        contract_hex = "0x" + contract_bytes.hex()

        result = await get_contract_schema_for_code(self.msg_handler, contract_hex)

        assert result == self.mock_schema
        # Verify the node was called with decoded bytes
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == contract_bytes

    @pytest.mark.asyncio
    async def test_valid_hex_encoded_without_prefix(self):
        """Test successful schema retrieval with hex-encoded contract code without 0x prefix."""
        # Encode contract as hex (without 0x prefix)
        contract_bytes = self.simple_contract.encode("utf-8")
        contract_hex = contract_bytes.hex()

        result = await get_contract_schema_for_code(self.msg_handler, contract_hex)

        assert result == self.mock_schema
        # Verify the node was called with decoded bytes
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == contract_bytes

    @pytest.mark.asyncio
    async def test_plain_utf8_string_fallback(self):
        """Test fallback to UTF-8 encoding when contract code is not hex."""
        # Pass plain contract code (not hex-encoded)
        result = await get_contract_schema_for_code(
            self.msg_handler, self.simple_contract
        )

        assert result == self.mock_schema
        # Verify the node was called with UTF-8 encoded bytes
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == self.simple_contract.encode("utf-8")

    @pytest.mark.asyncio
    async def test_non_hex_string_with_special_characters(self):
        """Test UTF-8 fallback with contract code containing non-ASCII characters."""
        # Contract with non-ASCII characters (comments with unicode)
        contract_with_unicode = """from genlayer import *

@gl_class
class UnicodeContract:
    # This is a comment with émojis 🚀 and spëcial çharacters
    def __init__(self):
        self.message = "Hello 世界"
"""

        result = await get_contract_schema_for_code(
            self.msg_handler, contract_with_unicode
        )

        assert result == self.mock_schema
        # Verify the node was called with UTF-8 encoded bytes
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == contract_with_unicode.encode("utf-8")

    @pytest.mark.asyncio
    async def test_empty_string(self):
        """Test handling of empty string."""
        result = await get_contract_schema_for_code(self.msg_handler, "")

        assert result == self.mock_schema
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == b""

    @pytest.mark.asyncio
    async def test_short_hex_string(self):
        """Test handling of short hex strings."""
        short_hex = "0x1234"

        result = await get_contract_schema_for_code(self.msg_handler, short_hex)

        assert result == self.mock_schema
        # Should decode the hex successfully
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == bytes.fromhex("1234")

    @pytest.mark.asyncio
    async def test_mixed_case_hex_string(self):
        """Test handling of mixed-case hex strings."""
        contract_bytes = b"test contract"
        mixed_case_hex = "0x" + "".join(
            c.upper() if i % 2 else c.lower()
            for i, c in enumerate(contract_bytes.hex())
        )

        result = await get_contract_schema_for_code(self.msg_handler, mixed_case_hex)

        assert result == self.mock_schema
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == contract_bytes

    @pytest.mark.asyncio
    async def test_invalid_json_in_schema_response(self):
        """Test error handling when schema response is invalid JSON."""
        # Make the mock return invalid JSON
        self.mock_node_instance.get_contract_schema = AsyncMock(
            return_value="invalid json {"
        )

        with pytest.raises(json.JSONDecodeError):
            await get_contract_schema_for_code(self.msg_handler, self.simple_contract)

    @pytest.mark.asyncio
    async def test_logging_on_utf8_fallback(self, caplog):
        """Test that logging occurs when falling back to UTF-8 encoding."""
        import logging

        with caplog.at_level(logging.DEBUG):
            await get_contract_schema_for_code(self.msg_handler, self.simple_contract)

        # Check that a debug log was created
        assert any(
            "Contract code is not hex-encoded" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_long_contract_code_hex(self):
        """Test handling of very long hex-encoded contract code."""
        # Create a long contract (simulate a real-world contract)
        long_contract = self.simple_contract * 10  # Repeat to make it longer
        contract_bytes = long_contract.encode("utf-8")
        contract_hex = "0x" + contract_bytes.hex()

        result = await get_contract_schema_for_code(self.msg_handler, contract_hex)

        assert result == self.mock_schema
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == contract_bytes

    @pytest.mark.asyncio
    async def test_long_contract_code_utf8(self):
        """Test handling of very long UTF-8 contract code."""
        # Create a long contract (simulate a real-world contract)
        long_contract = self.simple_contract * 10  # Repeat to make it longer

        result = await get_contract_schema_for_code(self.msg_handler, long_contract)

        assert result == self.mock_schema
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == long_contract.encode("utf-8")

    @pytest.mark.asyncio
    async def test_hex_with_odd_length(self):
        """Test handling of hex strings with odd length (should fail hex decode, use UTF-8)."""
        # Odd-length hex string (invalid)
        odd_hex = "0x123"  # 3 hex chars is odd

        # This should fall back to UTF-8 encoding since it's invalid hex
        result = await get_contract_schema_for_code(self.msg_handler, odd_hex)

        assert result == self.mock_schema
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        # Should be UTF-8 encoded of the string "0x123"
        assert call_args == odd_hex.encode("utf-8")

    @pytest.mark.asyncio
    async def test_whitespace_handling(self):
        """Test handling of contract code with various whitespace."""
        contract_with_whitespace = f"\n\n{self.simple_contract}\n\n"

        result = await get_contract_schema_for_code(
            self.msg_handler, contract_with_whitespace
        )

        assert result == self.mock_schema
        self.mock_node_instance.get_contract_schema.assert_called_once()
        call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
        assert call_args == contract_with_whitespace.encode("utf-8")

    @pytest.mark.asyncio
    async def test_bytes_like_string_patterns(self):
        """Test handling of strings that might look like encoded data."""
        patterns = [
            "base64:SGVsbG8gV29ybGQ=",  # Looks like base64 but isn't hex
            "data:text/plain,hello",  # Data URI format
            "\\x48\\x65\\x6c\\x6c\\x6f",  # Escaped hex notation
        ]

        for pattern in patterns:
            self.mock_node_instance.reset_mock()
            result = await get_contract_schema_for_code(self.msg_handler, pattern)

            assert result == self.mock_schema
            self.mock_node_instance.get_contract_schema.assert_called_once()
            call_args = self.mock_node_instance.get_contract_schema.call_args[0][0]
            # Should all be treated as UTF-8 strings
            assert call_args == pattern.encode("utf-8")
