import hashlib

from backend.node.genvm.origin.public_abi import root_offsets


def get_code_slot() -> bytes:
    offset = root_offsets.CODE.to_bytes(4, byteorder="little", signed=False)
    return hashlib.sha3_256(b"\x00" * 32 + offset).digest()
