"""Failing repro: _repr_result_with_capped_data raises on a non-JSON raw_error.

_repr_result_with_capped_data (node/base.py) is called on every GenVM execution
(_execution_finished). Its docstring promises it "falls back to the default
repr if parsing fails", but the except handler re-executes the exact same
failing expression:

    try:
        as_str = f"{result!r}"   # ExecutionError.__repr__ -> json.dumps(...)
        ...
    except Exception:
        return f"{result!r}"     # <- re-runs the same failing repr, re-raises

ExecutionError.__repr__ does json.dumps({... "raw_error": self.raw_error}), and
raw_error is populated verbatim from calldata-decoded GenVM error data, which
natively carries bytes/Address values -- not JSON-serializable. So a USER_ERROR
whose error dict contains any bytes/Address value crashes execution-result
logging and aborts _run_genvm with a TypeError instead of producing an error
receipt.
"""

from backend.node.base import _repr_result_with_capped_data
from backend.node.genvm.base import ExecutionError
from backend.node.genvm.origin.public_abi import ResultCode


def test_repr_result_with_bytes_raw_error_does_not_raise():
    err = ExecutionError(
        "boom",
        ResultCode.USER_ERROR,
        raw_error={"ctx": {"blob": b"\x01\x02"}},
    )
    # Must not raise; the function is documented to fall back to a repr.
    result = _repr_result_with_capped_data(err)
    assert isinstance(result, str)
