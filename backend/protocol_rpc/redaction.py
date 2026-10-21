"""Redaction of validator private keys from RPC-facing payloads.

Receipts carry the executing validator's ``node_config`` (including its
``private_key``); anything that returns a receipt to a client must pass
through :func:`sanitize_rpc_private_keys`. Shared by the JSON-RPC endpoints and
the explorer REST API.
"""

import os
from typing import Any


def show_validator_private_keys_in_rpc() -> bool:
    return os.getenv("SHOW_VALIDATOR_PRIVATE_KEYS_IN_RPC", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_private_key_field(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.replace("_", "").replace("-", "").lower()
    return normalized == "privatekey" or normalized.endswith("privatekey")


def sanitize_rpc_private_keys(value: Any) -> Any:
    """Return RPC data with private-key fields removed unless explicitly enabled."""
    if show_validator_private_keys_in_rpc():
        return value

    if isinstance(value, dict):
        return {
            key: sanitize_rpc_private_keys(item)
            for key, item in value.items()
            if not is_private_key_field(key)
        }
    if isinstance(value, list):
        return [sanitize_rpc_private_keys(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_rpc_private_keys(item) for item in value)
    return value
