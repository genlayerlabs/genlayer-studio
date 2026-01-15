import hashlib


def get_code_slot() -> bytes:
    return hashlib.sha3_256(b"\x00" * 32 + b"\x01\x00\x00\x00").digest()
