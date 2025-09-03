# backend/rollup/web3_pool.py

import os
import threading
from web3 import Web3
from web3.providers.rpc import HTTPProvider
import requests


class Web3ConnectionPool:
    """
    Thread-safe singleton class to manage Web3 connections to Hardhat.
    Ensures only one Web3 instance is created and reused across the application.
    """
    _web3 = None
    _lock = threading.Lock()
    _session = None

    @classmethod
    def get(cls):
        """
        Get the singleton Web3 instance with thread-safe initialization.
        Creates a new instance if one doesn't exist.
        
        Returns:
            Web3: The singleton Web3 instance connected to Hardhat
        """
        if cls._web3 is None:
            with cls._lock:
                if cls._web3 is None:
                    # Construct endpoint URL properly
                    base = os.environ.get("HARDHAT_URL", "http://127.0.0.1")
                    port = os.environ.get("HARDHAT_PORT", "8545")
                    
                    # Ensure scheme is present
                    endpoint = base if "://" in base else f"http://{base}"
                    
                    # Only append port if not already present in the URL
                    if ":" not in endpoint.rsplit("/", 1)[-1]:
                        endpoint = f"{endpoint}:{port}"
                    
                    # Configure connection pooling with HTTPAdapter
                    adapter = requests.adapters.HTTPAdapter(
                        pool_connections=1,
                        pool_maxsize=1
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
    def get_connection(cls):
        """
        Get the singleton Web3 instance.
        Alias for get() for backward compatibility.
        
        Returns:
            Web3: The singleton Web3 instance connected to Hardhat
        """
        return cls.get()