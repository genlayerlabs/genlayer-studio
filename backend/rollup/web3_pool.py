# backend/rollup/web3_pool.py

import os
import threading
from typing import ClassVar

import requests
from web3 import Web3
from web3.providers.rpc import HTTPProvider


class Web3ConnectionPool:
    """
    Thread-safe singleton class to manage Web3 connections to Hardhat.
    Ensures only one Web3 instance is created and reused across the application.
    """

    _web3: ClassVar[Web3 | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()
    _session: ClassVar[requests.Session | None] = None
    _offline: ClassVar[Web3 | None] = None

    @classmethod
    def get_for_utilities(cls) -> Web3:
        """A Web3 usable for pure helpers even with no rollup configured.

        HARDHAT_URL is optional — Studio runs without the legacy Hardhat
        bridge — so ``get()`` legitimately returns None. Hashing, ABI codec
        and checksum helpers still need a Web3 object and none of them touch
        the network, so they fall back to a provider-less instance rather
        than dereferencing None. Callers that actually reach the chain must
        keep using ``get()`` and handle None themselves.
        """
        web3 = cls.get()
        if web3 is not None:
            return web3
        if cls._offline is None:
            with cls._lock:
                if cls._offline is None:
                    cls._offline = Web3()
        return cls._offline

    @classmethod
    def get(cls) -> Web3 | None:
        """
        Get the singleton Web3 instance with thread-safe initialization.
        Creates a new instance if one doesn't exist.

        Returns:
            Web3 | None: The singleton Web3 instance connected to Hardhat, or None if
            HARDHAT_URL is not set
        """
        if cls._web3 is None:
            with cls._lock:
                if cls._web3 is None:
                    # Construct endpoint URL properly
                    base = os.environ.get("HARDHAT_URL")
                    if not base:
                        return None
                    port = os.environ.get("HARDHAT_PORT", "8545")

                    # Ensure scheme is present
                    endpoint = base if "://" in base else f"http://{base}"

                    # Only append port if not already present in the URL
                    if ":" not in endpoint.rsplit("/", 1)[-1]:
                        endpoint = f"{endpoint}:{port}"

                    # Configure connection pooling with HTTPAdapter
                    adapter = requests.adapters.HTTPAdapter(
                        pool_connections=1, pool_maxsize=1
                    )
                    cls._session = requests.Session()
                    cls._session.mount("http://", adapter)
                    cls._session.mount("https://", adapter)

                    # Create Web3 instance with configured session
                    cls._web3 = Web3(HTTPProvider(endpoint, session=cls._session))
        return cls._web3

    @classmethod
    def reset(cls):
        """
        Reset the singleton instance and properly close connections.
        Useful for testing or reconnection scenarios.
        """
        with cls._lock:
            if cls._web3:
                # Close provider session if available
                provider = cls._web3.provider
                cls._web3 = None
                if hasattr(provider, "session") and provider.session:
                    provider.session.close()

            # Close the session
            if cls._session:
                cls._session.close()
                cls._session = None

    @classmethod
    def close(cls):
        """
        Close the singleton instance and properly close connections.
        Alias for reset() for compatibility.
        """
        cls.reset()

    @classmethod
    def get_connection(cls) -> Web3 | None:
        """
        Get the singleton Web3 instance.
        Alias for get() for backward compatibility.

        Returns:
            Web3 | None: The singleton Web3 instance connected to Hardhat, or None if
            HARDHAT_URL is not set
        """
        return cls.get()
