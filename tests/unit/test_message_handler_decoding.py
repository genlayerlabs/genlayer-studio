import pytest
import base64
import struct
import json
from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.protocol_rpc.configuration import GlobalConfiguration
from flask_socketio import SocketIO
from backend.node.types import Address


class TestMessageHandlerDecoding:
    """Test the decoding functionality in MessageHandler."""

    @pytest.fixture
    def message_handler(self):
        """Create a MessageHandler instance for testing."""
        socketio = SocketIO()
        config = GlobalConfiguration()
        return MessageHandler(socketio, config)

    @pytest.fixture(autouse=True)
    def disable_truncation(self, monkeypatch):
        """Disable log truncation for all tests in this class to focus on decoding logic."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    def test_contract_code_decoding(self, message_handler):
        """Test that contract code is decoded to readable Python."""
        # Plain UTF-8 contract code
        contract_code = '# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n\nfrom genlayer import *\n\nclass Storage(gl.Contract):\n    storage: str'
        b64_encoded = base64.b64encode(contract_code.encode("utf-8")).decode("ascii")

        # Test in contract_code context
        test_data = {"contract_code": b64_encoded}
        result = message_handler._decode_value(test_data)
        assert result["contract_code"] == contract_code

    def test_genvm_encoded_contract_code(self, message_handler):
        """Test that GenVM-encoded contract code with headers is decoded correctly."""
        # Contract code with GenVM header (0xf5 prefix)
        contract_code = (
            '# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n\nfrom genlayer import *'
        )
        genvm_data = b"\xf5\x01\x00\x00" + contract_code.encode("utf-8")
        b64_encoded = base64.b64encode(genvm_data).decode("ascii")

        # Test in contract_state context where GenVM decoding should happen
        test_data = {"contract_state": {"code_slot": b64_encoded}}
        result = message_handler._decode_value(test_data)
        assert result["contract_state"]["code_slot"] == contract_code

    def test_32_byte_storage_remains_hex(self, message_handler):
        """Test that 32-byte storage values remain as hex (no integer decoding)."""
        test_cases = [
            # (description, binary_data)
            ("1 as 32-byte", b"\x01" + b"\x00" * 31),
            ("20 as 32-byte", b"\x14" + b"\x00" * 31),
            ("34 as 32-byte", b"\x22" + b"\x00" * 31),
            ("255 as 32-byte", b"\xff" + b"\x00" * 31),
            ("256 as 32-byte", b"\x00\x01" + b"\x00" * 30),
        ]

        for description, binary_data in test_cases:
            b64_encoded = base64.b64encode(binary_data).decode("ascii")
            test_data = {"contract_state": {"storage_slot": b64_encoded}}
            result = message_handler._decode_value(test_data)
            # Storage slots remain as base64
            assert (
                result["contract_state"]["storage_slot"] == b64_encoded
            ), f"{description}: Expected {b64_encoded}, got {result['contract_state']['storage_slot']}"

    def test_small_binary_data_as_hex(self, message_handler):
        """Test that small binary data is shown as hex."""
        test_cases = [
            (b"\x00\x00", "0000"),
            (b"\xff\xfe", "fffe"),
            (b"\x01\x02\x03\x04", "01020304"),
        ]

        for binary_data, expected_hex in test_cases:
            b64_encoded = base64.b64encode(binary_data).decode("ascii")
            # Test in result context where hex decoding should happen
            test_data = {"result": b64_encoded}
            result = message_handler._decode_value(test_data)
            assert result["result"] == expected_hex

    def test_empty_strings(self, message_handler):
        """Test that empty strings are handled correctly."""
        result = message_handler._decode_value("")
        assert result == ""

        # Empty base64 data
        empty_b64 = base64.b64encode(b"").decode(
            "ascii"
        )  # This would be empty or invalid
        result = message_handler._decode_value(empty_b64)
        # Should either be empty string or the original value
        assert result in ("", empty_b64)

    def test_non_base64_strings(self, message_handler):
        """Test that non-base64 strings are left unchanged."""
        test_cases = [
            "hello world",
            "not-base64!",
            "transaction_hash",
            "0x1234567890abcdef",
        ]

        for test_string in test_cases:
            result = message_handler._decode_value(test_string)
            assert result == test_string

    def test_hex_decoding(self, message_handler):
        """Test that hex strings are not decoded without proper context."""
        # Hex strings without context should remain unchanged
        hex_string = "48656c6c6f"  # "Hello" in hex
        result = message_handler._decode_value(hex_string)
        assert result == hex_string  # Should remain unchanged without context

        # Invalid hex should be left unchanged
        invalid_hex = "48656c6c6g"  # Contains 'g'
        result = message_handler._decode_value(invalid_hex)
        assert result == invalid_hex

    def test_nested_data_structures(self, message_handler):
        """Test that nested dictionaries and lists are processed recursively."""
        test_data = {
            "contract_state": {
                "storage_key_1": "MQ==",
                "storage_key_2": base64.b64encode(b"\x22" + b"\x00" * 31).decode(
                    "ascii"
                ),
                "empty_key": "",
            },
            "calldata": {
                "args": ["MQ==", "MTIz"],  # Should decode to ["1", "123"]
            },
            "result": "AAA=",  # Should decode to "0000"
        }

        result = message_handler._decode_value(test_data)

        # Storage slots should remain as base64 (no integer decoding)
        assert result["contract_state"]["storage_key_1"] == "MQ=="  # "1" as base64
        assert result["contract_state"]["storage_key_2"] == base64.b64encode(
            b"\x22" + b"\x00" * 31
        ).decode(
            "ascii"
        )  # 34 as base64 (64 chars)
        assert result["contract_state"]["empty_key"] == ""
        assert result["calldata"]["args"] == ["1", "123"]
        assert result["result"] == "0000"

    def test_key_based_decoding_rules(self, message_handler):
        """Test hardcoded key-based decoding rules."""
        # Contract code should always be decoded as UTF-8
        contract_code = (
            "# v0.1.0\nfrom genlayer import *\nclass Storage(gl.Contract):\n    pass"
        )
        contract_code_b64 = base64.b64encode(contract_code.encode("utf-8")).decode(
            "ascii"
        )

        test_data = {
            "contract_code": contract_code_b64,
            "code": contract_code_b64,
            "contract_state": {
                "code_slot": contract_code_b64,
                "storage_slot": base64.b64encode(b"\x2a" + b"\x00" * 31).decode(
                    "ascii"
                ),
            },
            "result": base64.b64encode(b"\x00\x00\x00\x00").decode("ascii"),
            "calldata": "DgRhcmdzDZEC",
        }

        result = message_handler._decode_value(test_data)

        # Contract code fields should be decoded
        assert result["contract_code"] == contract_code
        assert result["code"] == contract_code

        # Contract state code slot should remain as base64 (not decoded unless it has GenVM headers)
        code_slot_result = result["contract_state"]["code_slot"]
        assert code_slot_result == contract_code_b64

        # Storage slot should also remain as base64
        assert result["contract_state"]["storage_slot"] == base64.b64encode(
            b"\x2a" + b"\x00" * 31
        ).decode(
            "ascii"
        )  # 42 as base64

        # Result should be hex
        assert result["result"] == "00000000"

        # Calldata should be decoded to readable format
        assert result["calldata"] == {"args": [34]}

    def test_direct_key_path_decoding(self, message_handler):
        """Test that we decode directly based on key paths without guessing."""
        test_data = {
            "args": [
                base64.b64encode(b"1").decode("ascii"),  # Simple string
                base64.b64encode(b"\x7b" + b"\x00" * 31).decode(
                    "ascii"
                ),  # 123 as 32-byte integer
            ]
        }

        result = message_handler._decode_value(test_data)

        # Args should be decoded by storage slot logic (text and GenVM, but not integers)
        assert result["args"][0] == "1"  # UTF-8 text
        # The 32-byte integer should fallback to base64 since it's not readable text or GenVM
        assert result["args"][1] == "ewAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    def test_is_readable_text(self, message_handler):
        """Test the _is_readable_text helper method."""
        # Readable text
        assert message_handler._is_readable_text("Hello World")
        assert message_handler._is_readable_text("# v0.1.0\nfrom genlayer import *")
        assert message_handler._is_readable_text("class Storage:\n    pass")

        # Non-readable text
        assert not message_handler._is_readable_text("")
        assert not message_handler._is_readable_text("Hello\x00World")
        assert not message_handler._is_readable_text("\x01\x02\x03")

    def test_storage_slot_edge_cases(self, message_handler):
        """Test edge cases in storage slot decoding."""
        # Empty bytes in storage context
        test_data = {
            "contract_state": {"empty_slot": base64.b64encode(b"").decode("ascii")}
        }
        result = message_handler._decode_value(test_data)
        assert result["contract_state"]["empty_slot"] == ""

        # Large text data in contract_state should remain as base64 (conservative approach)
        large_data = b"x" * 100
        b64_encoded = base64.b64encode(large_data).decode("ascii")
        test_data = {"contract_state": {"large_slot": b64_encoded}}
        result = message_handler._decode_value(test_data)
        assert result["contract_state"]["large_slot"] == b64_encoded

        # 32-byte data with all zeros should remain as base64
        all_zeros = b"\x00" * 32
        b64_encoded = base64.b64encode(all_zeros).decode("ascii")
        test_data = {"contract_state": {"zero_slot": b64_encoded}}
        result = message_handler._decode_value(test_data)
        assert result["contract_state"]["zero_slot"] == b64_encoded

    def test_real_log_data(self, message_handler):
        """Test with actual data from real logs."""
        real_data = {
            "contract_state": {
                "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": "9QEAACMgdjAuMS4wCiMgeyAiRGVwZW5kcyI6ICJweS1nZW5sYXllcjpsYXRlc3QiIH0K",
                "IbngE/dGCLkpR4YSh7PedsLAdv6Dm3mUdhvZUMwudWY=": "",
                "Ny1Gw62p+JfHTTSbv+DkUMeYFnyfWA+Nr4Xe9X6Ww+o=": "IgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            },
            "calldata": "DgRhcmdzDZEC",
            "result": "AAA=",
        }

        result = message_handler._decode_value(real_data)

        # Contract code should be decoded
        contract_code = result["contract_state"][
            "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec="
        ]
        assert contract_code == '# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n'

        # Empty value should stay empty
        assert (
            result["contract_state"]["IbngE/dGCLkpR4YSh7PedsLAdv6Dm3mUdhvZUMwudWY="]
            == ""
        )

        # Storage value should remain as base64 (no integer decoding)
        storage_value = result["contract_state"][
            "Ny1Gw62p+JfHTTSbv+DkUMeYFnyfWA+Nr4Xe9X6Ww+o="
        ]
        assert (
            storage_value == "IgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        )  # 34 as base64

        # Result should be hex
        assert result["result"] == "0000"

        # Calldata should be decoded to readable format
        assert result["calldata"] == {"args": [34]}

    def test_memoryview_handling(self, message_handler):
        """Test that memoryview objects are handled correctly."""
        test_data = memoryview(b"Hello World")
        # Without context, memoryview should be converted to hex
        result = message_handler._decode_value(test_data)
        assert result == "48656c6c6f20576f726c64"  # hex representation
        assert isinstance(result, str)

    def test_bytes_handling(self, message_handler):
        """Test that bytes objects are handled correctly."""
        test_data = b"Hello World"
        # Without context, bytes should be converted to hex
        result = message_handler._decode_value(test_data)
        assert result == "48656c6c6f20576f726c64"  # hex representation
        assert isinstance(result, str)

        # Binary bytes should be shown as hex
        binary_data = b"\x01\x02\x03\x04"
        result = message_handler._decode_value(binary_data)
        assert result == "01020304"

    def test_other_types_passthrough(self, message_handler):
        """Test that other data types are passed through unchanged."""
        test_cases = [
            42,
            True,
            False,
            None,
        ]

        for test_value in test_cases:
            result = message_handler._decode_value(test_value)
            assert result == test_value
            assert type(result) == type(test_value)

    def test_invalid_base64_handling(self, message_handler):
        """Test that invalid base64 strings are handled gracefully."""
        # Invalid base64 string (369 characters, not multiple of 4)
        invalid_b64 = "a" * 369  # This would cause the error from the logs

        test_cases = [
            ("contract_code", invalid_b64),
            ("contract_state", {"some_key": invalid_b64}),
            ("result", invalid_b64),
        ]

        for key, value in test_cases:
            test_data = {key: value}
            result = message_handler._decode_value(test_data)
            # Should return original value when base64 decoding fails
            if key == "contract_state":
                assert result[key]["some_key"] == invalid_b64
            else:
                assert result[key] == value

    def test_real_state_decoding(self, message_handler):
        """Test decoding with the actual log."""
        real_log_data = {
            "data": {
                "state": {
                    "accepted": {
                        "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": "9AEAACMgdjAuMS4wCiMgeyAiRGVwZW5kcyI6ICJweS1nZW5sYXllcjpsYXRlc3QiIH0K",
                        "v96pn90vMq46SFcdUOno3Af0EMen6CFMDp9zUiNNU5Y=": "MQ==",
                        "Ny1Gw62p+JfHTTSbv+DkUMeYFnyfWA+Nr4Xe9X6Ww+o=": "AQAAAA==",
                    },
                    "finalized": {
                        "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": "9AEAACMgdjAuMS4wCiMgeyAiRGVwZW5kcyI6ICJweS1nZW5sYXllcjpsYXRlc3QiIH0K",
                    },
                },
                "calldata": "DgRhcmdzDQwx",
            }
        }

        assert message_handler._decode_value(real_log_data) == {
            "data": {
                "state": {
                    "accepted": {
                        "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": '# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n',
                        "v96pn90vMq46SFcdUOno3Af0EMen6CFMDp9zUiNNU5Y=": "MQ==",
                        "Ny1Gw62p+JfHTTSbv+DkUMeYFnyfWA+Nr4Xe9X6Ww+o=": "AQAAAA==",
                    },
                    "finalized": {
                        "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": '# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n',
                    },
                },
                "calldata": {"args": ["1"]},
            }
        }

    def test_storage_slot_key_detection(self, message_handler):
        """Test that storage slot keys are correctly identified."""
        # Real storage slot keys from the logs (32-byte hashes encoded as base64)
        storage_keys = [
            "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=",  # 44 chars with =
            "v96pn90vMq46SFcdUOno3Af0EMen6CFMDp9zUiNNU5Y=",  # 44 chars with =
            "Ny1Gw62p+JfHTTSbv+DkUMeYFnyfWA+Nr4Xe9X6Ww+o=",  # 44 chars with =
        ]

        # Non-storage keys
        non_storage_keys = [
            "contract_code",
            "calldata",
            "result",
            "short_key",
            "invalid_base64_key!",
        ]

        for key in storage_keys:
            assert message_handler._is_storage_slot_key(
                key
            ), f"Should detect {key} as storage slot key"

        for key in non_storage_keys:
            assert not message_handler._is_storage_slot_key(
                key
            ), f"Should NOT detect {key} as storage slot key"

    def test_contract_code_in_state_decoding(self, message_handler):
        """Test that contract code in state with 0x00 0x02 format is decoded."""
        contract_code_with_header = (
            "AAIAACMgdjAuMS4wCiMgeyAiRGVwZW5kcyI6ICJweS1nZW5sYXllcjpsYXRlc3QiIH0K"
        )

        test_data = {
            "contract_state": {
                "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": contract_code_with_header
            }
        }

        result = message_handler._decode_value(test_data)

        decoded_code = result["contract_state"][
            "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec="
        ]
        assert decoded_code == '# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n'

    def test_already_decoded_log(self, message_handler):
        """Test with the already decoded log."""
        transaction_log = {
            "transaction": {
                "data": {
                    "calldata": {"args": [[1, 3]]},
                    "contract_code": '# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n\nfrom genlayer import *\n\n\n# contract class\nclass Storage(gl.Contract):\n    storage: DynArray[u256]\n\n    # constructor\n    def __init__(self, initial_storage: list):\n        self.storage = initial_storage',
                    "contract_address": "0x513984320146324dd1A8b7D6E25FAf9251050E76",
                }
            }
        }

        result = message_handler._decode_value(transaction_log)

        # Contract code should remain readable
        assert "# v0.1.0" in result["transaction"]["data"]["contract_code"]
        assert "class Storage" in result["transaction"]["data"]["contract_code"]

        # Calldata should remain as structured data
        assert result["transaction"]["data"]["calldata"] == {"args": [[1, 3]]}

    def test_consensus_log(self, message_handler):
        """Test with the consensus log."""
        consensus_log = {
            "consensus_data": {
                "leader_receipt": [
                    {
                        "calldata": {"args": [[1, 2]]},  # Already decoded
                        "contract_state": {
                            # Contract code with 0x00 0x02 prefix
                            "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": "AAIAACMgdjAuMS4wCiMgeyAiRGVwZW5kcyI6ICJweS1nZW5sYXllcjpsYXRlc3QiIH0KCmZyb20gZ2VubGF5ZXIgaW1wb3J0ICoKCgojIGNvbnRyYWN0IGNsYXNzCmNsYXNzIFN0b3JhZ2UoZ2wuQ29udHJhY3QpOgogICAgc3RvcmFnZTogRHluQXJyYXlbdTI1Nl0=",
                            # Empty storage slot
                            "IbngE/dGCLkpR4YSh7PedsLAdv6Dm3mUdhvZUMwudWY=": "",
                            # Large binary storage slot
                            "ugBWMHRazzAUqvFi6ZMwQDAsoL7z9W/i1zwKCPgsYQs=": "BAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhee6AFYwdFrPMBSq8WLpkzBAMCygvvP1b+LXPAoI+CxhCyG54BP3Rgi5KUeGEoez3nbCwHb+g5t5lHYb2VDMLnVm",
                            # 4-byte integer (base64 encoded)
                            "Ny1Gw62p+JfHTTSbv+DkUMeYFnyfWA+Nr4Xe9X6Ww+o=": "AgAAAA==",
                            # 64-byte storage slot with array data (contains [1, 2])
                            "v96pn90vMq46SFcdUOno3Af0EMen6CFMDp9zUiNNU5Y=": "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==",
                        },
                    }
                ]
            }
        }

        result = message_handler._decode_value(consensus_log)

        # Contract code in state should now be decoded
        contract_state = result["consensus_data"]["leader_receipt"][0]["contract_state"]
        contract_code = contract_state["4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec="]
        assert "# v0.1.0" in contract_code
        assert "class Storage" in contract_code
        assert "DynArray[u256]" in contract_code

        # Empty storage should stay empty
        assert contract_state["IbngE/dGCLkpR4YSh7PedsLAdv6Dm3mUdhvZUMwudWY="] == ""

        # Large binary data should be returned as base64 (too large to decode meaningfully)
        large_data = contract_state["ugBWMHRazzAUqvFi6ZMwQDAsoL7z9W/i1zwKCPgsYQs="]
        assert isinstance(large_data, str)
        assert len(large_data) > 50

        # Storage slots should remain as base64 (no integer decoding)
        assert (
            contract_state["Ny1Gw62p+JfHTTSbv+DkUMeYFnyfWA+Nr4Xe9X6Ww+o="] == "AgAAAA=="
        )

        # 64-byte storage slot should remain as base64 (no array decoding)
        array_storage = contract_state["v96pn90vMq46SFcdUOno3Af0EMen6CFMDp9zUiNNU5Y="]
        assert (
            array_storage
            == "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
        )

        # Calldata should remain structured
        assert result["consensus_data"]["leader_receipt"][0]["calldata"] == {
            "args": [[1, 2]]
        }

    def test_genvm_with_state_log(self, message_handler):
        """Test with the genvm log."""
        genvm_log = {
            "data": {
                "state": {
                    "accepted": {
                        "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": "AAIAACMgdjAuMS4wCiMgeyAiRGVwZW5kcyI6ICJweS1nZW5sYXllcjpsYXRlc3QiIH0KCmZyb20gZ2VubGF5ZXIgaW1wb3J0ICoKCgojIGNvbnRyYWN0IGNsYXNzCmNsYXNzIFN0b3JhZ2UoZ2wuQ29udHJhY3QpOgogICAgc3RvcmFnZTogRHluQXJyYXlbdTI1Nl0="
                    },
                    "finalized": {
                        "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": "AAIAACMgdjAuMS4wCiMgeyAiRGVwZW5kcyI6ICJweS1nZW5sYXllcjpsYXRlc3QiIH0KCmZyb20gZ2VubGF5ZXIgaW1wb3J0ICoKCgojIGNvbnRyYWN0IGNsYXNzCmNsYXNzIFN0b3JhZ2UoZ2wuQ29udHJhY3QpOgogICAgc3RvcmFnZTogRHluQXJyYXlbdTI1Nl0="
                    },
                },
                "code": '# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n\nfrom genlayer import *\n\n\n# contract class\nclass Storage(gl.Contract):\n    storage: DynArray[u256]',
            }
        }

        result = message_handler._decode_value(genvm_log)

        accepted_code = result["data"]["state"]["accepted"][
            "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec="
        ]
        assert "# v0.1.0" in accepted_code
        assert "DynArray[u256]" in accepted_code

        finalized_code = result["data"]["state"]["finalized"][
            "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec="
        ]
        assert "# v0.1.0" in finalized_code
        assert "DynArray[u256]" in finalized_code

        assert "# v0.1.0" in result["data"]["code"]
        assert "DynArray[u256]" in result["data"]["code"]

    def test_various_calldata_formats(self, message_handler):
        """Test different calldata formats."""

        test_cases = [
            ("DgRhcmdzDQwx", {"args": ["1"]}),
            ("DgRhcmdzDZEC", {"args": [34]}),
            ("DgRhcmdzDRUMMQwy", {"args": [["1", "2"]]}),
            ("DgRhcmdzDRUJEQ==", {"args": [[1, 2]]}),
            ("DgRhcmdzDQg=", {"args": [False]}),
            ("DgRhcmdzDRA=", {"args": [True]}),
            (
                "DgRhcmdzDRgAAAAAAAAAAAAAAAAAAAAAAAAAAA==",
                {"args": ["addr#0000000000000000000000000000000000000000"]},
            ),
            ("DgRhcmdzDRPerQ==", {"args": ["b#dead"]}),
        ]

        for calldata_b64, expected_result in test_cases:
            test_data = {"calldata": calldata_b64}
            result = message_handler._decode_value(test_data)
            assert result["calldata"] == expected_result, f"Failed for {calldata_b64}"

    def test_genvm_format_detection(self, message_handler):
        """Test that different GenVM formats are correctly detected and decoded."""
        # Test 0x00 0x02 format (contract code in state)
        contract_code_0002 = (
            "AAIAACMgdjAuMS4wCiMgeyAiRGVwZW5kcyI6ICJweS1nZW5sYXllcjpsYXRlc3QiIH0K"
        )
        test_data = {"contract_state": {"code_key": contract_code_0002}}
        result = message_handler._decode_value(test_data)
        assert "# v0.1.0" in result["contract_state"]["code_key"]

        # Test regular base64 without special headers (should try GenVM first for calldata)
        calldata_no_header = (
            "DgRhcmdzDQwx"  # This doesn't start with 0xf4/0xf5 or 0x00/0x02
        )
        test_data = {"calldata": calldata_no_header}
        result = message_handler._decode_value(test_data)
        assert result["calldata"] == {"args": ["1"]}  # Should still decode via GenVM

    def test_storage_slots_remain_as_hex(self, message_handler):
        """Test that storage slots are not decoded as integers."""
        test_cases = [
            ("Single byte", b"\x01"),
            ("Two bytes", bytes.fromhex("fbff")),
            ("Four bytes", struct.pack("<L", 1000)),
            ("32-byte integer", b"\x05" + b"\x00" * 31),
        ]

        for description, test_bytes in test_cases:
            b64_encoded = base64.b64encode(test_bytes).decode("ascii")
            test_data = {"contract_state": {"storage_slot": b64_encoded}}
            result = message_handler._decode_value(test_data)

            assert (
                result["contract_state"]["storage_slot"] == b64_encoded
            ), f"{description}: expected {b64_encoded}, got {result['contract_state']['storage_slot']}"

    def test_contract_code_with_args_list_prefix(self, message_handler):
        """Test that contract code with 0x01 0x02 prefix (used with args list) is decoded correctly."""
        # Contract code with 0x01 0x02 prefix with args list
        contract_code_0102 = "AQIAACMgdjAuMS4wCiMgeyAiRGVwZW5kcyI6ICJweS1nZW5sYXllcjpsYXRlc3QiIH0KCmZyb20gZ2VubGF5ZXIgaW1wb3J0ICoKCgojIGNvbnRyYWN0IGNsYXNzCmNsYXNzIFN0b3JhZ2UoZ2wuQ29udHJhY3QpOgogICAgc3RvcmFnZTogRHluQXJyYXlbdTI1Nl0gCgogICAgIyBjb25zdHJ1Y3RvcgogICAgZGVmIF9faW5pdF9fKHNlbGYsIGluaXRpYWxfc3RvcmFnZTogbGlzdCk6CiAgICAgICAgc2VsZi5zdG9yYWdlID0gaW5pdGlhbF9zdG9yYWdlCgogICAgIyByZWFkIG1ldGhvZHMgbXVzdCBiZSBhbm5vdGF0ZWQgd2l0aCB2aWV3CiAgICBAZ2wucHVibGljLnZpZXcKICAgIGRlZiBnZXRfc3RvcmFnZShzZWxmKSAtPiBzdHI6CiAgICAgICAgcmV0dXJuIHNlbGYuc3RvcmFnZQoKICAgICMgd3JpdGUgbWV0aG9kCiAgICBAZ2wucHVibGljLndyaXRlCiAgICBkZWYgdXBkYXRlX3N0b3JhZ2Uoc2VsZiwgbmV3X3N0b3JhZ2U6IHN0cikgLT4gTm9uZToKICAgICAgICBzZWxmLnN0b3JhZ2UgPSBuZXdfc3RvcmFnZQ=="

        # Test in contract_state context where GenVM decoding should happen
        test_data = {"contract_state": {"code_slot": contract_code_0102}}
        result = message_handler._decode_value(test_data)

        decoded_code = result["contract_state"]["code_slot"]
        assert isinstance(decoded_code, str)
        assert "# v0.1.0" in decoded_code
        assert "class Storage" in decoded_code
        assert "DynArray[u256]" in decoded_code
        assert "def __init__" in decoded_code
        assert "def get_storage" in decoded_code
        assert "def update_storage" in decoded_code

    def test_memoryview_objects_handling(self, message_handler):
        """Test that memoryview objects are properly converted for JSON serialization."""
        # Test data with memoryview objects
        test_data = {
            "contract_state": {
                "code_key": memoryview(b"# v0.1.0\nclass Storage:\n    pass"),
                "storage_key": memoryview(b"\xde\xad"),  # b#dead as bytes
                "nested": {"inner_key": memoryview(b"nested_data")},
            },
            "calldata": {"args": [memoryview(b"args_data")]},
            "result": memoryview(b"result_data"),
        }

        # This should not raise an AttributeError about memoryview.__dict__
        result = message_handler._decode_value(test_data)

        # All memoryview objects should be converted to strings (base64 or hex)
        assert isinstance(result["contract_state"]["code_key"], str)
        assert isinstance(result["contract_state"]["storage_key"], str)
        assert isinstance(result["contract_state"]["nested"]["inner_key"], str)
        assert isinstance(result["calldata"]["args"][0], str)
        assert isinstance(result["result"], str)

        # Test that the result can be JSON serialized without errors
        json_str = json.dumps(result, default=lambda o: o.__dict__)
        assert len(json_str) > 0

        # Verify the decoded content makes sense (storage slots remain as base64)
        assert (
            result["contract_state"]["storage_key"] == "3q0="
        )  # Base64 representation of b'\xde\xad'

        # Test args case - should be decoded and converted to hex if non-ASCII
        args_data = {"calldata": {"args": [memoryview(b"\xde\xad")]}}
        args_result = message_handler._decode_value(args_data)
        args_final = message_handler._convert_non_serializable_objects(args_result)
        assert args_final["calldata"]["args"][0] == "dead"

    def test_address_objects_handling(self, message_handler):
        """Test that Address objects are properly converted for JSON serialization."""
        zero_address = Address("0x0000000000000000000000000000000000000000")
        test_address = Address("0x27faa0498AdfdF9D10E160BEe8Db1f95703f4cBf")

        test_data = {
            "contract_state": {
                "address_key": zero_address,
                "another_address": test_address,
                "nested": {"inner_address": zero_address},
            },
            "calldata": {"args": [zero_address, test_address]},
            "result": test_address,
        }

        # This should not raise an AttributeError about Address.__dict__
        result = message_handler._apply_log_level_truncation(test_data)

        # All Address objects should be converted to strings
        assert isinstance(result["contract_state"]["address_key"], str)
        assert isinstance(result["contract_state"]["another_address"], str)
        assert isinstance(result["contract_state"]["nested"]["inner_address"], str)
        assert isinstance(result["calldata"]["args"][0], str)
        assert isinstance(result["calldata"]["args"][1], str)
        assert isinstance(result["result"], str)

        # Verify the addresses are in the expected format
        assert (
            result["contract_state"]["address_key"]
            == "addr#0000000000000000000000000000000000000000"
        )
        assert "addr#" in result["contract_state"]["another_address"]
        assert "addr#" in result["calldata"]["args"][0]
        assert "addr#" in result["calldata"]["args"][1]

        # Test that the result can be JSON serialized without errors
        json_str = json.dumps(result, default=lambda o: o.__dict__)
        assert len(json_str) > 0

        # Verify the zero address case specifically
        assert (
            result["calldata"]["args"][0]
            == "addr#0000000000000000000000000000000000000000"
        )

    def test_data_field_decoding(self, message_handler):
        """Test that 'data' fields within JSON strings are decoded to human-readable format used in [GenVM] execution finished log."""
        base64_data_1 = {
            "result": '{"kind": "return", "data": "vBB7ImN0b3IiOnsicGFyYW1zIjpbWyJpbml0aWFsX3N0b3JhZ2UiLCJieXRlcyJdXSwia3dwYXJhbXMiOnt9fSwibWV0aG9kcyI6eyJnZXRfc3RvcmFnZSI6eyJwYXJhbXMiOltdLCJrd3BhcmFtcyI6e30sInJlYWRvbmx5Ijp0cnVlLCJyZXQiOiJzdHJpbmcifSwidXBkYXRlX3N0b3JhZ2UiOnsicGFyYW1zIjpbWyJuZXdfc3RvcmFnZSIsInN0cmluZyJdXSwia3dwYXJhbXMiOnt9LCJyZWFkb25seSI6ZmFsc2UsInJldCI6Im51bGwiLCJwYXlhYmxlIjpmYWxzZX19fQ=="}'
        }
        base64_data_2 = {
            "result": '{"kind": "return", "data": "xBB7ImN0b3IiOnsicGFyYW1zIjpbWyJpbml0aWFsX3N0b3JhZ2UiLCJzdHJpbmciXV0sImt3cGFyYW1zIjp7fX0sIm1ldGhvZHMiOnsiZ2V0X3N0b3JhZ2UiOnsicGFyYW1zIjpbXSwia3dwYXJhbXMiOnt9LCJyZWFkb25seSI6dHJ1ZSwicmV0Ijoic3RyaW5nIn0sInVwZGF0ZV9zdG9yYWdlIjp7InBhcmFtcyI6W1sibmV3X3N0b3JhZ2UiLCJzdHJpbmciXV0sImt3cGFyYW1zIjp7fSwicmVhZG9ubHkiOmZhbHNlLCJyZXQiOiJudWxsIiwicGF5YWJsZSI6ZmFsc2V9fX0="}'
        }

        for log_data in [base64_data_1, base64_data_2]:
            result = message_handler._decode_value(log_data)

            # The result should now be a parsed object (not a JSON string) with decoded data
            parsed_result = result["result"]
            assert isinstance(parsed_result, dict)
            assert parsed_result["kind"] == "return"

            # The data field should be decoded to readable JSON
            decoded_data = parsed_result["data"]
            assert isinstance(decoded_data, str)
            assert "ctor" in decoded_data
            assert "methods" in decoded_data
            assert "get_storage" in decoded_data
            assert "update_storage" in decoded_data

    def test_zip_file_contract_code_handling(self, message_handler):
        """Test that ZIP files in contract_code field are kept as base64, not converted to hex."""
        zip_base64 = "UEsDBAoAAAAAAIpxDFsAAAAAAAAAAAAAAAAJAAAAY29udHJhY3QvUEsDBAoAAAAAAIpxDFuf/XMU"

        # Test direct decoding
        zip_bytes = base64.b64decode(zip_base64)
        result = message_handler._decode_bytes_by_key(zip_bytes, "contract_code")

        # Should remain as base64, not convert to hex
        assert result == zip_base64
        assert not result.startswith("504b03040a")  # Should not be hex

        # Test in full log structure
        test_data = {"data": {"contract_code": zip_base64}}

        decoded_result = message_handler._decode_value(test_data)

        # Should keep ZIP as base64 in the full structure
        assert decoded_result["data"]["contract_code"] == zip_base64
