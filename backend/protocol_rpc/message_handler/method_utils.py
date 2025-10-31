from __future__ import annotations

from typing import Any, List

from eth_utils.address import to_checksum_address
from eth_account import Account


def extract_account_address_from_rpc(method_name: str, params: Any) -> str | None:
    """Extract account address from JSON-RPC method name and params.

    Supports both positional list params and named dict params, including
    eth_sendRawTransaction recovery.
    """

    def _normalize(addr: Any) -> str | None:
        if not isinstance(addr, str):
            return None
        try:
            return to_checksum_address(addr)
        except Exception:
            return None

    try:
        args: List[Any]
        if isinstance(params, list):
            args = params
        elif isinstance(params, dict):
            # Named params path
            if method_name in ["eth_getBalance", "eth_getTransactionCount"]:
                candidate = (
                    params.get("address") or params.get("account") or params.get("0")
                )
                return _normalize(candidate)
            elif method_name in [
                "eth_sendTransaction",
                "eth_call",
                "gen_call",
                "eth_estimateGas",
            ]:
                tx_obj = params.get("params") or params.get("transaction") or params
                if isinstance(tx_obj, dict) and "from" in tx_obj:
                    return _normalize(tx_obj.get("from"))
            elif method_name == "eth_sendRawTransaction":
                raw = (
                    params.get("0")
                    or params.get("data")
                    or params.get("rawTransaction")
                )
                if isinstance(raw, str):
                    try:
                        sender = Account.recover_transaction(raw)
                        return _normalize(sender)
                    except Exception:
                        return None
            return None
        else:
            args = [params]

        # Positional params path
        if (
            method_name in ["eth_getBalance", "eth_getTransactionCount"]
            and len(args) >= 1
        ):
            return _normalize(args[0])
        elif (
            method_name
            in [
                "eth_sendTransaction",
                "eth_call",
                "gen_call",
                "eth_estimateGas",
            ]
            and len(args) >= 1
        ):
            if isinstance(args[0], dict) and "from" in args[0]:
                return _normalize(args[0]["from"])
        elif (
            method_name == "eth_sendRawTransaction"
            and len(args) >= 1
            and isinstance(args[0], str)
        ):
            try:
                sender = Account.recover_transaction(args[0])
                return _normalize(sender)
            except Exception:
                return None
        return None
    except Exception:
        return None


def extract_transaction_hash_from_rpc(method_name: str, params: Any) -> str | None:
    """Extract transaction hash for methods that take a tx hash.

    Supports positional list params and named dict params.
    """
    try:
        # Common methods that accept a transaction hash as first param
        hash_methods = {"eth_getTransactionReceipt", "eth_getTransactionByHash"}
        if method_name not in hash_methods:
            return None

        # Positional style
        if isinstance(params, list) and params:
            first = params[0]
            return first if isinstance(first, str) else None

        # Named style
        if isinstance(params, dict):
            for key in ("hash", "transaction_hash", "0"):
                value = params.get(key)
                if isinstance(value, str):
                    return value
        return None
    except Exception:
        return None
