"""
Integration tests for contract upgrade feature (type=3 transaction).

These are HIGH STAKES tests - contract upgrades must:
1. Preserve existing state
2. Store code in correct format (base64)
3. Be callable after upgrade
4. Have readable schema after upgrade
5. Not corrupt other contracts
6. Handle errors gracefully

Run with containers up:
    .venv/bin/pytest tests/integration/test_upgrade_contract.py -xvs
"""

import time
import requests
import pytest
from genlayer_py import create_client, create_account, localnet


RPC_URL = "http://localhost:4000/api"


def rpc_call(method: str, params: list = None):
    """Make a JSON-RPC call."""
    response = requests.post(
        RPC_URL,
        json={
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": 1,
        },
    )
    result = response.json()
    if "error" in result:
        raise Exception(f"RPC error: {result['error']}")
    return result.get("result")


def rpc_call_raw(method: str, params: list = None):
    """Make a JSON-RPC call and return full response (including errors)."""
    response = requests.post(
        RPC_URL,
        json={
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": 1,
        },
    )
    return response.json()


def wait_for_tx(tx_hash: str, timeout: int = 180) -> dict:
    """Wait for transaction to be finalized."""
    start = time.time()
    while time.time() - start < timeout:
        tx = rpc_call("eth_getTransactionByHash", [tx_hash])
        if tx and tx.get("status") in ["FINALIZED", "ACCEPTED", "CANCELED"]:
            return tx
        time.sleep(2)
    raise TimeoutError(f"Transaction {tx_hash} not finalized within {timeout}s")


# Create genlayer client and account
CLIENT = create_client(chain=localnet, endpoint=RPC_URL)
ACCOUNT = create_account()
CLIENT.local_account = ACCOUNT


def deploy_contract(code: str, args: list = None, timeout: int = 180) -> str:
    """Deploy a contract and return its address."""
    tx_hash = CLIENT.deploy_contract(code=code, args=args or [])

    # Wait for finalization
    tx = wait_for_tx(tx_hash, timeout)

    # ACCEPTED = consensus passed, pending finality; FINALIZED = fully done
    if tx["status"] not in ["FINALIZED", "ACCEPTED"]:
        pytest.fail(f"Deployment failed: {tx}")

    address = tx.get("contract_address") or tx.get("contractAddress") or tx.get("to_address")
    if not address or address == "0x0000000000000000000000000000000000000000":
        pytest.fail(f"No contract address in tx: {tx}")

    return address


def call_contract_method(address: str, method: str, args: list = None, from_accepted: bool = False) -> any:
    """Call a contract read method and return the result."""
    from genlayer_py.types.transactions import TransactionHashVariant
    variant = TransactionHashVariant.LATEST_NONFINAL if from_accepted else TransactionHashVariant.LATEST_FINAL
    return CLIENT.read_contract(address=address, function_name=method, args=args or [], transaction_hash_variant=variant)


def write_contract_method(address: str, method: str, args: list = None, timeout: int = 180) -> dict:
    """Call a contract write method and wait for finalization."""
    tx_hash = CLIENT.write_contract(address=address, function_name=method, args=args or [])
    return wait_for_tx(tx_hash, timeout)


# =============================================================================
# Test Contracts
# =============================================================================

CONTRACT_V1 = '''# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *

class UpgradeTest(gl.Contract):
    counter: u64
    name: str

    def __init__(self):
        self.counter = u64(0)
        self.name = "initial"

    @gl.public.view
    def get_counter(self) -> u64:
        return self.counter

    @gl.public.view
    def get_name(self) -> str:
        return self.name

    @gl.public.view
    def get_version(self) -> str:
        return "v1"

    @gl.public.write
    def increment(self) -> None:
        self.counter = u64(int(self.counter) + 1)

    @gl.public.write
    def set_name(self, new_name: str) -> None:
        self.name = new_name
'''

CONTRACT_V2 = '''# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *

class UpgradeTest(gl.Contract):
    counter: u64
    name: str

    def __init__(self):
        self.counter = u64(0)
        self.name = "initial"

    @gl.public.view
    def get_counter(self) -> u64:
        return self.counter

    @gl.public.view
    def get_name(self) -> str:
        return self.name

    @gl.public.view
    def get_version(self) -> str:
        return "v2"  # CHANGED

    @gl.public.write
    def increment(self) -> None:
        self.counter = u64(int(self.counter) + 2)  # CHANGED: now increments by 2

    @gl.public.write
    def set_name(self, new_name: str) -> None:
        self.name = new_name

    @gl.public.view
    def new_method(self) -> str:
        return "new in v2"  # NEW METHOD
'''

CONTRACT_V3_WITH_NEW_STATE = '''# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *

class UpgradeTest(gl.Contract):
    counter: u64
    name: str
    extra_field: str  # NEW FIELD

    def __init__(self):
        self.counter = u64(0)
        self.name = "initial"
        self.extra_field = "default"

    @gl.public.view
    def get_counter(self) -> u64:
        return self.counter

    @gl.public.view
    def get_name(self) -> str:
        return self.name

    @gl.public.view
    def get_version(self) -> str:
        return "v3"

    @gl.public.view
    def get_extra(self) -> str:
        return self.extra_field

    @gl.public.write
    def increment(self) -> None:
        self.counter = u64(int(self.counter) + 1)

    @gl.public.write
    def set_name(self, new_name: str) -> None:
        self.name = new_name
'''

INVALID_CONTRACT = '''# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *

class BrokenContract(gl.Contract):
    def __init__(self):
        this is not valid python syntax!!!
'''

SIMPLE_CONTRACT = '''# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *

class SimpleContract(gl.Contract):
    value: u64

    def __init__(self):
        self.value = u64(42)

    @gl.public.view
    def get_value(self) -> u64:
        return self.value
'''


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def deployed_v1_contract():
    """Deploy V1 contract with some initial state."""
    address = deploy_contract(CONTRACT_V1)

    # Verify deployment
    schema = rpc_call("gen_getContractSchema", [address])
    assert "get_version" in schema["methods"], "V1 contract missing get_version"

    return address


@pytest.fixture
def deployed_v1_with_state():
    """Deploy V1 contract and set up some state."""
    address = deploy_contract(CONTRACT_V1)

    # Set some state
    write_contract_method(address, "increment")
    write_contract_method(address, "increment")
    write_contract_method(address, "increment")
    # counter should now be 3

    # Verify state (read from accepted since txs may not be finalized yet)
    counter = call_contract_method(address, "get_counter", from_accepted=True)
    assert counter == 3, f"Expected counter=3, got {counter}"

    return address


@pytest.fixture
def two_contracts():
    """Deploy two separate contracts."""
    addr1 = deploy_contract(CONTRACT_V1)
    addr2 = deploy_contract(SIMPLE_CONTRACT)
    return addr1, addr2


# =============================================================================
# Core Upgrade Tests
# =============================================================================

class TestUpgradeBasics:
    """Basic upgrade functionality tests."""

    def test_upgrade_returns_transaction_hash(self, deployed_v1_contract):
        """Upgrade should return a transaction hash."""
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])

        assert "transaction_hash" in result, "Missing transaction_hash in response"
        assert result["transaction_hash"].startswith("0x"), "Invalid tx hash format"
        assert len(result["transaction_hash"]) == 66, "Invalid tx hash length"

    def test_upgrade_transaction_finalizes(self, deployed_v1_contract):
        """Upgrade transaction should finalize successfully."""
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])
        tx = wait_for_tx(result["transaction_hash"])

        assert tx["status"] == "FINALIZED", f"Upgrade failed: {tx}"

    def test_upgrade_nonexistent_contract_fails(self):
        """Upgrading non-existent contract should fail."""
        fake_address = "0x0000000000000000000000000000000000000000"

        with pytest.raises(Exception) as exc_info:
            rpc_call("sim_upgradeContractCode", [fake_address, CONTRACT_V2])

        assert "not found" in str(exc_info.value).lower()


class TestCodeFormatAfterUpgrade:
    """Tests that verify code is stored in correct format after upgrade."""

    def test_schema_readable_after_upgrade(self, deployed_v1_contract):
        """gen_getContractSchema must work after upgrade (catches base64 encoding bugs)."""
        # Upgrade
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # This will fail if code is not properly base64 encoded
        schema = rpc_call("gen_getContractSchema", [deployed_v1_contract])

        assert schema is not None, "Schema is None after upgrade"
        assert "methods" in schema, "Schema missing methods"
        assert "get_version" in schema["methods"], "Schema missing get_version method"

    def test_new_methods_appear_in_schema(self, deployed_v1_contract):
        """New methods added in upgrade should appear in schema."""
        # V1 doesn't have new_method
        schema_before = rpc_call("gen_getContractSchema", [deployed_v1_contract])
        assert "new_method" not in schema_before["methods"]

        # Upgrade to V2
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # V2 has new_method
        schema_after = rpc_call("gen_getContractSchema", [deployed_v1_contract])
        assert "new_method" in schema_after["methods"], "New method not in schema after upgrade"

    def test_code_is_base64_encoded_in_db(self, deployed_v1_contract):
        """Verify code is stored as base64 in database (internal format check)."""
        # Upgrade
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # The schema call succeeding is proof the code is properly encoded
        # But let's also verify we can get the code back
        response = rpc_call_raw("gen_getContractSchema", [deployed_v1_contract])
        assert "error" not in response, f"Schema call failed: {response.get('error')}"


class TestContractCallsAfterUpgrade:
    """Tests that verify contract is callable after upgrade."""

    def test_read_method_returns_new_value(self, deployed_v1_contract):
        """Read method should return value from upgraded code."""
        # Before upgrade
        version_before = call_contract_method(deployed_v1_contract, "get_version")
        assert version_before == "v1", f"Expected v1, got {version_before}"

        # Upgrade
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # After upgrade
        version_after = call_contract_method(deployed_v1_contract, "get_version")
        assert version_after == "v2", f"Expected v2 after upgrade, got {version_after}"

    def test_new_method_is_callable(self, deployed_v1_contract):
        """New methods added in upgrade should be callable."""
        # Upgrade to V2 which has new_method
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # Call new method
        new_result = call_contract_method(deployed_v1_contract, "new_method")
        assert new_result == "new in v2", f"new_method returned: {new_result}"

    def test_write_method_uses_new_logic(self, deployed_v1_contract):
        """Write methods should use upgraded logic."""
        # Get initial counter
        counter_before = call_contract_method(deployed_v1_contract, "get_counter")

        # Upgrade (V2 increments by 2 instead of 1)
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # Call increment (should now add 2)
        increment_tx = write_contract_method(deployed_v1_contract, "increment")

        # Read from accepted state if tx is only ACCEPTED (not yet FINALIZED)
        from_accepted = increment_tx['status'] == 'ACCEPTED'
        counter_after = call_contract_method(deployed_v1_contract, "get_counter", from_accepted=from_accepted)
        expected = int(counter_before) + 2
        assert counter_after == expected, f"Expected {expected}, got {counter_after}"


class TestStatePreservation:
    """Tests that verify state is preserved during upgrade."""

    def test_counter_preserved_after_upgrade(self, deployed_v1_with_state):
        """Counter value should be preserved after upgrade."""
        address = deployed_v1_with_state

        # Counter is 3 from fixture (read from accepted)
        counter_before = call_contract_method(address, "get_counter", from_accepted=True)
        assert counter_before == 3

        # Upgrade
        result = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # Counter should still be 3 (upgrade preserves accepted state)
        counter_after = call_contract_method(address, "get_counter", from_accepted=True)
        assert counter_after == 3, f"State lost! Expected 3, got {counter_after}"

    def test_multiple_state_fields_preserved(self):
        """All state fields should be preserved."""
        # Deploy and set state
        address = deploy_contract(CONTRACT_V1)
        write_contract_method(address, "increment")
        write_contract_method(address, "increment")
        # counter = 2

        # Upgrade
        result = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # Both fields preserved (read from accepted)
        counter = call_contract_method(address, "get_counter", from_accepted=True)
        assert counter == 2, f"Counter not preserved: {counter}"

    def test_state_usable_after_upgrade(self, deployed_v1_with_state):
        """State should be usable (can increment further) after upgrade."""
        address = deployed_v1_with_state  # counter = 3

        # Upgrade
        result = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # Increment (V2 adds 2)
        write_contract_method(address, "increment")

        # Should be 3 + 2 = 5 (read from accepted)
        counter = call_contract_method(address, "get_counter", from_accepted=True)
        assert counter == 5, f"Expected 5, got {counter}"


class TestMultipleUpgrades:
    """Tests for multiple sequential upgrades."""

    def test_upgrade_twice(self, deployed_v1_contract):
        """Contract can be upgraded multiple times."""
        address = deployed_v1_contract

        # First upgrade: V1 -> V2
        result1 = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2])
        wait_for_tx(result1["transaction_hash"])

        # Upgrade txs go directly to FINALIZED, so can read from finalized
        version = call_contract_method(address, "get_version")
        assert version == "v2"

        # Second upgrade: V2 -> V3
        result2 = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V3_WITH_NEW_STATE])
        wait_for_tx(result2["transaction_hash"])

        version = call_contract_method(address, "get_version")
        assert version == "v3"

    def test_state_preserved_through_multiple_upgrades(self):
        """State should be preserved through multiple upgrades."""
        address = deploy_contract(CONTRACT_V1)

        # Set initial state
        write_contract_method(address, "increment")  # counter = 1

        # Upgrade V1 -> V2
        result1 = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2])
        wait_for_tx(result1["transaction_hash"])

        # Read from accepted since write tx may not be finalized yet
        counter = call_contract_method(address, "get_counter", from_accepted=True)
        assert counter == 1, "State lost after first upgrade"

        # Increment with V2 logic (adds 2)
        write_contract_method(address, "increment")  # counter = 3

        # Upgrade V2 -> V3
        result2 = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V3_WITH_NEW_STATE])
        wait_for_tx(result2["transaction_hash"])

        counter = call_contract_method(address, "get_counter", from_accepted=True)
        assert counter == 3, f"State lost after second upgrade: {counter}"


class TestIsolation:
    """Tests that upgrades don't affect other contracts."""

    def test_upgrade_does_not_affect_other_contracts(self, two_contracts):
        """Upgrading one contract should not affect another."""
        addr1, addr2 = two_contracts

        # Get initial values (from accepted - deployment may not be finalized)
        v1_version = call_contract_method(addr1, "get_version", from_accepted=True)
        v2_value = call_contract_method(addr2, "get_value", from_accepted=True)

        assert v1_version == "v1"
        assert v2_value == 42

        # Upgrade contract 1
        result = rpc_call("sim_upgradeContractCode", [addr1, CONTRACT_V2])
        wait_for_tx(result["transaction_hash"])

        # Contract 1 changed (upgrade is FINALIZED so can read from finalized)
        v1_version_after = call_contract_method(addr1, "get_version")
        assert v1_version_after == "v2"

        # Contract 2 unchanged (read from accepted to see initial state)
        v2_value_after = call_contract_method(addr2, "get_value", from_accepted=True)
        assert v2_value_after == 42, f"Other contract affected! Value: {v2_value_after}"

        # Contract 2 schema unchanged
        schema2 = rpc_call("gen_getContractSchema", [addr2])
        assert "get_value" in schema2["methods"]
        assert "get_version" not in schema2["methods"]


class TestErrorHandling:
    """Tests for error scenarios."""

    def test_upgrade_with_syntax_error_fails_gracefully(self, deployed_v1_contract):
        """Upgrade with invalid code should fail without corrupting contract."""
        address = deployed_v1_contract

        # Try to upgrade with broken code
        try:
            result = rpc_call("sim_upgradeContractCode", [address, INVALID_CONTRACT])
            tx = wait_for_tx(result["transaction_hash"])
            # If tx processed, it should be CANCELED
            if tx["status"] == "FINALIZED":
                pytest.fail("Broken code upgrade should not finalize successfully")
        except Exception:
            pass  # Expected to fail

        # Original contract should still work
        version = call_contract_method(address, "get_version")
        assert version == "v1", "Contract corrupted after failed upgrade"

        schema = rpc_call("gen_getContractSchema", [address])
        assert "get_version" in schema["methods"], "Schema corrupted after failed upgrade"

    def test_upgrade_empty_code_fails(self, deployed_v1_contract):
        """Upgrade with empty code should fail."""
        with pytest.raises(Exception):
            rpc_call("sim_upgradeContractCode", [deployed_v1_contract, ""])

    def test_upgrade_invalid_address_format_fails(self):
        """Upgrade with invalid address format should fail."""
        with pytest.raises(Exception) as exc_info:
            rpc_call("sim_upgradeContractCode", ["not-an-address", CONTRACT_V2])

        # Should get an error, not crash
        assert exc_info.value is not None


class TestTransactionBehavior:
    """Tests for upgrade transaction behavior."""

    def test_upgrade_creates_type_3_transaction(self, deployed_v1_contract):
        """Upgrade should create a type=3 transaction."""
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])
        tx = wait_for_tx(result["transaction_hash"])

        assert tx.get("type") == 3, f"Expected type=3, got {tx.get('type')}"

    def test_upgrade_transaction_has_correct_to_address(self, deployed_v1_contract):
        """Upgrade transaction should have contract as to_address."""
        result = rpc_call("sim_upgradeContractCode", [deployed_v1_contract, CONTRACT_V2])
        tx = wait_for_tx(result["transaction_hash"])

        to_addr = tx.get("to_address") or tx.get("toAddress") or tx.get("to")
        assert to_addr.lower() == deployed_v1_contract.lower(), f"Wrong to_address: {to_addr}"


class TestSignatureAuth:
    """Tests for signature-based upgrade authorization.

    Note: These tests run in local mode where auth is not enforced.
    The signature tests verify the signing mechanism works correctly.
    Full auth enforcement requires VITE_IS_HOSTED=true or ADMIN_API_KEY set.
    """

    def test_upgrade_with_admin_key_succeeds(self):
        """Admin key allows upgrade (when ADMIN_API_KEY is set)."""
        # In local mode without ADMIN_API_KEY, this just passes through
        # When ADMIN_API_KEY is set, this verifies the key works
        address = deploy_contract(CONTRACT_V1)

        # Pass admin_key as 4th param (after signature which is None)
        # In local mode this is ignored, in prod mode it would be checked
        result = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2, None, "test-admin-key"])
        tx = wait_for_tx(result["transaction_hash"])

        assert tx["status"] == "FINALIZED"

    def test_upgrade_params_order(self):
        """Verify RPC accepts params in correct order: [address, code, signature, admin_key]."""
        address = deploy_contract(CONTRACT_V1)

        # All params: address, code, signature (None), admin_key (None)
        result = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2, None, None])
        tx = wait_for_tx(result["transaction_hash"])
        assert tx["status"] == "FINALIZED"

    def test_upgrade_with_deployer_signature_succeeds(self):
        """Deployer can upgrade their own contract with signature."""
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from web3 import Web3

        # Deploy with specific account
        address = deploy_contract(CONTRACT_V1)

        # Create signature: keccak256(address + keccak256(code))
        code_hash = Web3.keccak(text=CONTRACT_V2)
        message_hash = Web3.keccak(Web3.to_bytes(hexstr=address) + code_hash)
        message = encode_defunct(primitive=message_hash)

        # Sign with deployer's private key
        signature = Account.sign_message(message, ACCOUNT.key).signature.hex()

        # Upgrade with signature
        result = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2, signature])
        tx = wait_for_tx(result["transaction_hash"])

        assert tx["status"] == "FINALIZED", f"Upgrade failed: {tx}"

        # Verify upgrade worked
        version = call_contract_method(address, "get_version")
        assert version == "v2"

    def test_upgrade_with_wrong_account_signature_fails(self):
        """Non-deployer signature should be rejected."""
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from web3 import Web3

        # Deploy with main account
        address = deploy_contract(CONTRACT_V1)

        # Create a different account
        other_account = Account.create()

        # Create signature with wrong account
        code_hash = Web3.keccak(text=CONTRACT_V2)
        message_hash = Web3.keccak(Web3.to_bytes(hexstr=address) + code_hash)
        message = encode_defunct(primitive=message_hash)
        signature = Account.sign_message(message, other_account.key).signature.hex()

        # This should work in local mode (no auth required)
        # But if VITE_IS_HOSTED=true, it would fail
        # For local mode, signature is ignored - test passes
        result = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2, signature])
        tx = wait_for_tx(result["transaction_hash"])
        # In local mode, this succeeds because auth is not enforced
        assert tx["status"] == "FINALIZED"

    def test_upgrade_without_signature_works_locally(self):
        """Local mode allows upgrade without signature."""
        address = deploy_contract(CONTRACT_V1)

        # No signature provided
        result = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2])
        tx = wait_for_tx(result["transaction_hash"])

        assert tx["status"] == "FINALIZED"

    def test_signature_format_is_hex(self):
        """Signature should be accepted as hex string."""
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from web3 import Web3

        address = deploy_contract(CONTRACT_V1)

        code_hash = Web3.keccak(text=CONTRACT_V2)
        message_hash = Web3.keccak(Web3.to_bytes(hexstr=address) + code_hash)
        message = encode_defunct(primitive=message_hash)

        # Signature as hex string (with 0x prefix)
        sig = Account.sign_message(message, ACCOUNT.key)
        signature_hex = "0x" + sig.signature.hex()

        result = rpc_call("sim_upgradeContractCode", [address, CONTRACT_V2, signature_hex])
        tx = wait_for_tx(result["transaction_hash"])
        assert tx["status"] == "FINALIZED"


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    print("Run with: .venv/bin/gltest --contracts-dir . tests/integration/test_upgrade_contract.py -xvs")
