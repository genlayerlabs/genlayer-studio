import hashlib

from backend.node.genvm.origin.public_abi import root_offsets


def get_code_slot(*, legacy: bool = False) -> bytes:
    # The retained v0.2 ABI has CODE=1, LOCKED_SLOTS=2. The v0.3 ABI added a
    # contract indirection and moved them to 2 and 3 respectively.
    offset = (1 if legacy else root_offsets.CODE).to_bytes(
        4, byteorder="little", signed=False
    )
    return hashlib.sha3_256(b"\x00" * 32 + offset).digest()
