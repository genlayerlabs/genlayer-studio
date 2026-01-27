"""
SDK-based contract schema extraction using Python reflection.

This module provides a faster alternative to GenVM-based schema extraction
by using the genlayer-py-std SDK's reflection capabilities directly.
Performance improvement: ~50-100ms vs ~200-300ms with GenVM.

The approach:
1. Mock _genlayer_wasi (the WASI module that provides storage/balance ops)
2. Set GENERATING_DOCS=true to enable doc-generation mode in the SDK
3. Import the contract source and find the Contract class
4. Use genlayer.py.get_schema() for reflection-based schema extraction
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

logger = logging.getLogger(__name__)

# Simple in-memory cache with TTL
_schema_cache: dict[str, tuple[dict, float]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 300  # 5 minutes

# SDK paths relative to GENVMROOT
_SDK_SRC_SUBPATH = "runners/genlayer-py-std/src"
_SDK_EMB_SUBPATH = "runners/genlayer-py-std/src-emb"


def _get_sdk_paths() -> tuple[Path, Path] | None:
    """Get SDK paths from GENVMROOT environment variable."""
    genvmroot = os.environ.get("GENVMROOT")
    if not genvmroot:
        logger.debug("GENVMROOT not set, SDK schema extraction unavailable")
        return None

    root = Path(genvmroot)
    sdk_src = root / _SDK_SRC_SUBPATH
    sdk_emb = root / _SDK_EMB_SUBPATH

    # In Docker, GenVM is extracted to /genvm but SDK paths may not exist
    # Fall back to checking if the executor contains the SDK
    if not sdk_src.exists():
        # Try alternate location in executor
        tag = os.environ.get("GENVM_TAG", "")
        alt_root = root / "executor" / tag
        sdk_src_alt = alt_root / "runners" / "genlayer-py-std" / "src"
        if sdk_src_alt.exists():
            sdk_src = sdk_src_alt
            sdk_emb = alt_root / "runners" / "genlayer-py-std" / "src-emb"

    if not sdk_src.exists():
        logger.debug(f"SDK source path not found: {sdk_src}")
        return None

    return sdk_src, sdk_emb


def _setup_wasi_mocks() -> None:
    """Mock the _genlayer_wasi module that provides WASI bindings."""
    if "_genlayer_wasi" in sys.modules:
        return  # Already mocked

    wasi_mock = MagicMock()
    wasi_mock.storage_read = MagicMock(return_value=None)
    wasi_mock.storage_write = MagicMock(return_value=None)
    wasi_mock.get_balance = MagicMock(return_value=0)
    wasi_mock.get_self_balance = MagicMock(return_value=0)
    wasi_mock.gl_call = MagicMock(return_value=0)
    sys.modules["_genlayer_wasi"] = wasi_mock


def _get_cache_key(code: bytes) -> str:
    """Generate cache key from contract code hash."""
    import hashlib

    return hashlib.sha256(code).hexdigest()


def _get_cached_schema(code: bytes) -> dict | None:
    """Get schema from cache if valid."""
    key = _get_cache_key(code)
    with _cache_lock:
        if key in _schema_cache:
            schema, timestamp = _schema_cache[key]
            if time.time() - timestamp < _CACHE_TTL_SECONDS:
                logger.debug(f"SDK schema cache hit for {key[:8]}...")
                return schema
            else:
                # Expired, remove from cache
                del _schema_cache[key]
    return None


def _cache_schema(code: bytes, schema: dict) -> None:
    """Cache schema with timestamp."""
    key = _get_cache_key(code)
    with _cache_lock:
        _schema_cache[key] = (schema, time.time())
        # Simple cache size limit
        if len(_schema_cache) > 100:
            # Remove oldest entries
            sorted_entries = sorted(
                _schema_cache.items(), key=lambda x: x[1][1]
            )
            for old_key, _ in sorted_entries[:20]:
                del _schema_cache[old_key]


def extract_schema_via_sdk(contract_code: bytes) -> dict | None:
    """
    Extract contract schema using SDK reflection.

    Args:
        contract_code: Contract source code as bytes (UTF-8 encoded Python)

    Returns:
        Schema dict if successful, None if SDK extraction failed.
        Caller should fall back to GenVM-based extraction on None.

    Performance: ~50-100ms vs ~200-300ms with GenVM
    """
    # Check cache first
    cached = _get_cached_schema(contract_code)
    if cached is not None:
        return cached

    sdk_paths = _get_sdk_paths()
    if sdk_paths is None:
        return None

    sdk_src, sdk_emb = sdk_paths
    start_time = time.time()

    try:
        # Setup environment
        _setup_wasi_mocks()
        os.environ["GENERATING_DOCS"] = "true"

        # Add SDK paths to sys.path temporarily
        original_path = sys.path.copy()
        sys.path.insert(0, str(sdk_src))
        if sdk_emb.exists():
            sys.path.insert(0, str(sdk_emb))

        try:
            # Import the schema extraction function
            from genlayer.py.get_schema import get_schema

            # Write contract to temp file and load it as a module
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".py", delete=False
            ) as f:
                f.write(contract_code)
                temp_path = f.name

            try:
                # Load contract module
                spec = importlib.util.spec_from_file_location(
                    "__sdk_contract__", temp_path
                )
                if spec is None or spec.loader is None:
                    logger.debug("Failed to create module spec for contract")
                    return None

                module = importlib.util.module_from_spec(spec)
                sys.modules["__sdk_contract__"] = module
                spec.loader.exec_module(module)

                # Find the Contract class
                contract_class = None
                for name, obj in vars(module).items():
                    if isinstance(obj, type) and name != "Contract":
                        # Check if it inherits from Contract
                        bases = [b.__name__ for b in obj.__mro__]
                        if "Contract" in bases:
                            contract_class = obj
                            break

                if contract_class is None:
                    logger.debug("No Contract class found in module")
                    return None

                # Extract schema using SDK reflection
                schema = get_schema(contract_class)

                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    f"SDK schema extraction succeeded in {elapsed_ms}ms "
                    f"for {contract_class.__name__}"
                )

                # Cache the result
                _cache_schema(contract_code, schema)

                return schema

            finally:
                # Cleanup temp file
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                # Remove from sys.modules
                sys.modules.pop("__sdk_contract__", None)

        finally:
            # Restore sys.path
            sys.path = original_path

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.debug(
            f"SDK schema extraction failed after {elapsed_ms}ms: {e}",
            exc_info=True,
        )
        return None


def clear_cache() -> None:
    """Clear the schema cache (useful for testing)."""
    with _cache_lock:
        _schema_cache.clear()
