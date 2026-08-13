"""Failing repro: calldata.encode(memoryview) emits headerless bytes.

Every supported calldata type writes a (len<<3 | TYPE_*) ULEB128 header before
its payload. The memoryview branch (calldata.py: `mem.extend(b.tolist())`)
writes the raw payload with NO header, so the output is not valid calldata.
memoryview is explicitly special-cased and decode(..., memview2bytes=...) can
yield memoryviews that get re-encoded, so this corrupts round-trips: a
memoryview either fails to decode or silently decodes to a wrong value,
clobbering sibling fields in composite structures.
"""

import backend.node.genvm.origin.calldata as calldata


def test_memoryview_encodes_like_bytes():
    assert calldata.encode(memoryview(b"abc")) == calldata.encode(b"abc")


def test_memoryview_round_trips_in_map():
    # Silent corruption: the headerless single byte 0x11 decodes as the int 2,
    # so the map value is wrong (and it can bleed into sibling fields).
    decoded = calldata.decode(calldata.encode({"a": memoryview(b"\x11"), "b": 1}))
    assert decoded == {"a": b"\x11", "b": 1}
