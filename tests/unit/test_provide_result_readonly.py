from unittest.mock import MagicMock

from backend.node.genvm.base import Context, Host
from backend.node.genvm.origin.base_host import RunHostAndProgramRes
from backend.node.genvm.origin.public_abi import ResultCode


def _result_with_storage_change():
    return RunHostAndProgramRes(
        stdout="",
        stderr="",
        genvm_log=[],
        execution_time=0,
        execution_hash=b"",
        result_kind=ResultCode.RETURN,
        result_data=b"ok",
        result_fingerprint=None,
        result_storage_changes=[(b"\x11" * 32 + (0).to_bytes(4, "big"), b"\xaa")],
        result_emissions=[],
        result_nondet_results=[],
        data_fees_remaining=[],
    )


def test_provide_result_discards_storage_changes_for_readonly_state():
    host = Host(
        MagicMock(),
        calldata_bytes=b"",
        state_proxy=MagicMock(),
        leader_results=None,
    )
    state = MagicMock()
    state.readonly = True
    state.storage_write.side_effect = AssertionError("readonly write")

    host.provide_result(_result_with_storage_change(), state, Context())

    state.storage_write.assert_not_called()


def test_provide_result_applies_storage_changes_for_writable_state():
    host = Host(
        MagicMock(),
        calldata_bytes=b"",
        state_proxy=MagicMock(),
        leader_results=None,
    )
    state = MagicMock()
    state.readonly = False

    host.provide_result(_result_with_storage_change(), state, Context())

    state.storage_write.assert_called_once_with(b"\x11" * 32, 0, b"\xaa")
