"""Centralized configuration for Hardhat settings."""

import os
from web3 import Web3


class HardhatConfig:
    """Configuration class for Hardhat network settings."""

    @staticmethod
    def get_port() -> str:
        """Get the Hardhat port from environment variable."""
        return os.environ.get("HARDHAT_PORT", "8545")

    @staticmethod
    def get_base_url() -> str:
        """Get the Hardhat base URL from environment variable."""
        return os.environ.get("HARDHAT_URL", "http://localhost")

    @staticmethod
    def get_full_url() -> str:
        """Get the complete Hardhat URL with port."""
        port = HardhatConfig.get_port()
        url = HardhatConfig.get_base_url()
        return f"{url}:{port}"

    @staticmethod
    def get_web3_instance() -> Web3:
        """Get a Web3 instance connected to Hardhat network."""
        hardhat_url = HardhatConfig.get_full_url()
        return Web3(Web3.HTTPProvider(hardhat_url))
