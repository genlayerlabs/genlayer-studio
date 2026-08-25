import collections.abc
from dataclasses import dataclass
from typing import Self

_PADDING = b"padded"


@dataclass
class LeaderPublicData:
    nondet_block_outputs: list[bytes]

    def encode(self) -> bytes:
        payload = b"".join(
            _encode_bytes(value) for value in (*self.nondet_block_outputs, _PADDING)
        )
        return _encode_length(len(payload), 0xC0, 0xF7) + payload

    @classmethod
    def decode(cls, encoded: collections.abc.Buffer) -> Self:
        data = bytes(encoded)
        if not data:
            return cls([])

        payload_start, payload_len = _decode_length(data, 0, list_=True)
        payload_end = payload_start + payload_len
        if payload_end != len(data):
            raise ValueError("trailing leader public data")

        outputs: list[bytes] = []
        cursor = payload_start
        while cursor < payload_end:
            value_start, value_len = _decode_length(data, cursor, list_=False)
            value_end = value_start + value_len
            if value_end > payload_end:
                raise ValueError("leader public data item exceeds list")
            outputs.append(data[value_start:value_end])
            cursor = value_end

        if not outputs or outputs[-1] != _PADDING:
            raise ValueError("leader public data padding is missing")
        return cls(outputs[:-1])


def encode(value: LeaderPublicData) -> bytes:
    return value.encode()


def decode(encoded: collections.abc.Buffer) -> LeaderPublicData:
    return LeaderPublicData.decode(encoded)


def _encode_bytes(value: bytes) -> bytes:
    if len(value) == 1 and value[0] < 0x80:
        return value
    return _encode_length(len(value), 0x80, 0xB7) + value


def _encode_length(length: int, short_base: int, long_base: int) -> bytes:
    if length <= 55:
        return bytes([short_base + length])
    length_bytes = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([long_base + len(length_bytes)]) + length_bytes


def _decode_length(
    encoded: bytes,
    offset: int,
    *,
    list_: bool,
) -> tuple[int, int]:
    if offset >= len(encoded):
        raise ValueError("truncated leader public data")

    prefix = encoded[offset]
    short_base = 0xC0 if list_ else 0x80
    long_base = 0xF7 if list_ else 0xB7
    if not list_ and prefix < 0x80:
        return offset, 1
    if prefix < short_base or prefix > long_base + 8:
        raise ValueError("invalid RLP prefix")
    if prefix <= long_base:
        if not list_ and prefix == 0x81 and offset + 1 < len(encoded):
            if encoded[offset + 1] < 0x80:
                raise ValueError("non-canonical RLP string")
        return offset + 1, prefix - short_base

    length_len = prefix - long_base
    length_start = offset + 1
    length_end = length_start + length_len
    if length_end > len(encoded):
        raise ValueError("truncated RLP length")
    length_bytes = encoded[length_start:length_end]
    if length_bytes[0] == 0:
        raise ValueError("non-canonical RLP length")
    length = int.from_bytes(length_bytes, "big")
    if length <= 55:
        raise ValueError("non-canonical long RLP value")
    return length_end, length
