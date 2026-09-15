import pytest

from backend.node.genvm.origin import calldata as gvm_calldata
from backend.node.genvm.origin.leader_public_data import (
    LeaderPublicData,
    decode,
    encode,
)


def test_round_trip_from_memoryview() -> None:
    data = LeaderPublicData([b"a", b"bc"])

    assert decode(memoryview(encode(data))) == data


def test_has_stable_encoding() -> None:
    assert LeaderPublicData([b"a", b"bc"]).encode() == (
        b"\x0e\x07nd_outs\x15\x0ba\x13bc"
    )


@pytest.mark.parametrize(
    "encoded",
    [
        b"",
        b"\xcc\x84test\x86padded",
        gvm_calldata.encode({}),
        gvm_calldata.encode({"nd_outs": [1]}),
        gvm_calldata.encode({"nd_outs": []}) + b"\x00",
    ],
)
def test_rejects_invalid_encoding(encoded: bytes) -> None:
    with pytest.raises((ValueError, gvm_calldata.DecodingError)):
        LeaderPublicData.decode(encoded)
