"""`ConsumedResult.decode`: turning a manager-reported `consumed_result` blob
into a `ConsumedResult`.

`None`, empty bytes, and malformed bytes are three different situations and
must not be conflated: `None` means the manager never reported a result;
empty bytes means it reported one with nothing in it; malformed bytes is a
protocol violation, not a result value at all, so it raises rather than
quietly turning into an `internal_error(...)` result the caller could mistake
for a real (if unhappy) execution outcome.
"""

import base64

import pytest

import backend.node.genvm.origin.calldata as gvm_calldata
from backend.node.genvm.origin import public_abi
from backend.node.genvm.origin.base_host import (
    ConsumedResult,
    ConsumedResultDecodeError,
)


def _encode_valid(result_kind: public_abi.ResultCode, data: dict) -> bytes:
    return bytes([int(result_kind)]) + gvm_calldata.encode(data)


def test_decode_none_is_an_internal_error_result():
    result = ConsumedResult.decode(None)
    assert result.result_kind == public_abi.ResultCode.INTERNAL_ERROR
    assert result.result_data == "no_result"


def test_decode_empty_bytes_is_an_internal_error_result():
    result = ConsumedResult.decode(b"")
    assert result.result_kind == public_abi.ResultCode.INTERNAL_ERROR
    assert result.result_data == "empty_result"


def test_decode_valid_bytes_round_trips():
    raw = _encode_valid(public_abi.ResultCode.RETURN, {"execution_hash": b"\x01\x02"})
    result = ConsumedResult.decode(raw)
    assert result.result_kind == public_abi.ResultCode.RETURN
    assert result.execution_hash == b"\x01\x02"


def test_decode_not_a_mapping_is_an_internal_error_result():
    raw = bytes([int(public_abi.ResultCode.RETURN)]) + gvm_calldata.encode([1, 2, 3])
    result = ConsumedResult.decode(raw)
    assert result.result_kind == public_abi.ResultCode.INTERNAL_ERROR
    assert result.result_data == "result is not a mapping"


def test_decode_truncated_calldata_raises_a_protocol_error():
    # A ResultCode byte with no calldata payload behind it at all.
    with pytest.raises(ConsumedResultDecodeError):
        ConsumedResult.decode(bytes([int(public_abi.ResultCode.RETURN)]))


def test_decode_invalid_result_code_raises_a_protocol_error():
    raw = bytes([255]) + gvm_calldata.encode({})
    with pytest.raises(ConsumedResultDecodeError):
        ConsumedResult.decode(raw)


def test_decode_base64_string_round_trips():
    # The socket reports bytes, the deprecated http shim reports base64.
    raw = _encode_valid(public_abi.ResultCode.RETURN, {"execution_hash": b"\x01\x02"})
    result = ConsumedResult.decode(base64.b64encode(raw).decode())
    assert result.result_kind == public_abi.ResultCode.RETURN
    assert result.execution_hash == b"\x01\x02"


def test_decode_a_non_bytes_shaped_value_raises_a_protocol_error():
    # Reading `raw` can fail (a string that is not base64, or an int outside
    # 0-255 in a list) before there is even a ResultCode byte to look at.
    with pytest.raises(ConsumedResultDecodeError):
        ConsumedResult.decode("not bytes")
    with pytest.raises(ConsumedResultDecodeError):
        ConsumedResult.decode([1, 2, 999])


def test_decode_protocol_error_never_returns_an_internal_error_result():
    # Malformed bytes must not silently look like a legitimate (if unhappy)
    # execution outcome -- the caller has to be able to tell "the genvm ran
    # and failed" apart from "we can't tell what the genvm reported".
    with pytest.raises(ConsumedResultDecodeError) as exc_info:
        ConsumedResult.decode(bytes([int(public_abi.ResultCode.RETURN)]))
    assert not isinstance(exc_info.value, ConsumedResult)
