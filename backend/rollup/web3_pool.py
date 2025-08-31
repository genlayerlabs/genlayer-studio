# backend/rollup/web3_pool.py

import os
from web3 import Web3


class Web3ConnectionPool:
    """
    Singleton class to manage Web3 connections to Hardhat.
    Ensures only one Web3 instance is created and reused across the application.
    """
    _web3 = None

    @classmethod
    def get(cls):
        """
        Get the singleton Web3 instance.
        Creates a new instance if one doesn't exist.
        
        Returns:
            Web3: The singleton Web3 instance connected to Hardhat
        """
        if cls._web3 is None:
            port = os.environ.get("HARDHAT_PORT")
            url = os.environ.get("HARDHAT_URL")
            hardhat_url = f"{url}:{port}"
            cls._web3 = Web3(Web3.HTTPProvider(hardhat_url))
        return cls._web3

    @classmethod
    def reset(cls):
        """
        Reset the singleton instance.
        Useful for testing or reconnection scenarios.
        """
        cls._web3 = None