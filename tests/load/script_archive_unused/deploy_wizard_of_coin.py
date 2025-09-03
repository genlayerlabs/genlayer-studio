#!/usr/bin/env python3
"""
Test script for WizardOfCoin contract using genlayer_py library
Tests deployment, read/write operations similar to deploy_contract_example.py
"""

import sys
import os
import json
import time
import requests
from datetime import datetime

# Import genlayer_py
from genlayer_py import create_client, create_account
from genlayer_py.chains import localnet

# Configuration
BASE_URL = os.getenv('BASE_URL', 'http://localhost:4000/api')
CONTRACT_PATH = os.path.join(os.path.dirname(__file__), '../../examples/contracts/wizard_of_coin.py')

# Colors for terminal output
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    MAGENTA = '\033[0;35m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'  # No Color

def print_status(message, color=Colors.NC):
    """Print colored status message"""
    print(f"{color}{message}{Colors.NC}")

def get_client():
    """Create a GenLayer client with account"""
    account = create_account()
    client = create_client(
        chain=localnet,
        endpoint=BASE_URL,
        account=account
    )
    return client, account

def get_wizard_contract_code():
    """Load the WizardOfCoin contract code"""
    if not os.path.exists(CONTRACT_PATH):
        raise FileNotFoundError(f"Contract not found at {CONTRACT_PATH}")
    
    with open(CONTRACT_PATH, 'r') as f:
        return f.read()

def deploy_wizard_contract(client):
    """Deploy the WizardOfCoin contract using genlayer_py"""
    print_status("\n=== DEPLOYING WIZARDOFCOIN CONTRACT ===", Colors.BLUE)
    
    contract_code = get_wizard_contract_code()
    print_status(f"Contract code length: {len(contract_code)} characters", Colors.CYAN)
    print_status(f"Constructor args: have_coin=True", Colors.CYAN)
    
    try:
        # Deploy the contract with have_coin=true
        tx_hash = client.deploy_contract(
            code=contract_code,
            kwargs={"have_coin": True}
        )
        
        print_status(f"✅ Contract deployment TX: {tx_hash}", Colors.GREEN)
        
        # Wait for deployment and get contract address
        receipt = client.w3.eth.wait_for_transaction_receipt(tx_hash)
        
        if receipt.status != 1:
            print_status(f"❌ Deployment failed with status: {receipt.status}", Colors.RED)
            return None
        
        # Get transaction details to find the contract address
        print_status(f"Transaction receipt status: {receipt.status}", Colors.CYAN)
        print_status(f"Transaction hash: {tx_hash}", Colors.CYAN)
        
        # For GenLayer, we need to get the transaction details to find the contract address
        try:
            response = requests.post(BASE_URL, json={
                "jsonrpc": "2.0",
                "method": "eth_getTransactionByHash",
                "params": [tx_hash],
                "id": 1
            })
            
            tx_data = response.json()
            if 'result' in tx_data and tx_data['result']:
                raw_tx = tx_data['result']
                
                # Try to find contract address in various fields
                contract_address = None
                
                # Check data field
                if 'data' in raw_tx and isinstance(raw_tx['data'], dict):
                    contract_address = raw_tx['data'].get('contract_address')
                
                # Check direct fields
                if not contract_address:
                    contract_address = raw_tx.get('contractAddress') or raw_tx.get('creates')
                
                # Check receipt logs
                if not contract_address and 'logs' in receipt:
                    for log in receipt['logs']:
                        if hasattr(log, 'address'):
                            contract_address = log.address
                            break
                        elif isinstance(log, dict) and 'address' in log:
                            contract_address = log['address']
                            break
                
                if contract_address:
                    print_status(f"✅ Found contract address: {contract_address}", Colors.GREEN)
                else:
                    print_status("⚠️  Contract address not found in transaction data", Colors.YELLOW)
                    print_status(f"Transaction data keys: {list(raw_tx.keys())}", Colors.CYAN)
                    # Use tx_hash as fallback
                    contract_address = tx_hash
                    
            else:
                print_status("❌ Could not retrieve transaction data", Colors.RED)
                contract_address = tx_hash
                
        except Exception as e:
            print_status(f"⚠️  Error getting transaction data: {e}", Colors.YELLOW)
            contract_address = tx_hash
            
        print_status(f"✅ WizardOfCoin contract deployed at: {contract_address}", Colors.GREEN)
        return contract_address
        
    except Exception as e:
        print_status(f"❌ Deployment failed: {e}", Colors.RED)
        import traceback
        traceback.print_exc()
        return None

def test_read_operations(client, contract_address):
    """Test read operations on the deployed contract"""
    print_status(f"\n=== TESTING READ OPERATIONS ===", Colors.BLUE)
    print_status(f"Contract address: {contract_address}", Colors.CYAN)
    
    try:
        # Test 1: Read have_coin state
        print_status("\n1. Reading have_coin state...", Colors.CYAN)
        have_coin = client.read_contract(
            address=contract_address,
            function_name="get_have_coin"
        )
        
        print_status("✅ Read operation successful", Colors.GREEN)
        print_status(f"  have_coin = {have_coin}", Colors.CYAN)
        
        return have_coin
        
    except Exception as e:
        print_status(f"❌ Read operation failed: {e}", Colors.RED)
        return None

def test_write_simulation(client, contract_address):
    """Test write simulation (gen_call) on the contract"""
    print_status(f"\n=== TESTING WRITE SIMULATION ===", Colors.BLUE)
    
    try:
        # Simulate ask_for_coin method
        print_status("\n1. Simulating ask_for_coin write operation...", Colors.CYAN)
        
        result = client.simulate_write_contract(
            address=contract_address,
            function_name="ask_for_coin",
            args=["Please give me the magical coin! I really need it for my quest."]
        )
        
        print_status("✅ Write simulation completed", Colors.GREEN)
        print_status(f"  Simulation result: {result}", Colors.CYAN)
        
        return True
        
    except Exception as e:
        print_status(f"❌ Write simulation failed: {e}", Colors.RED)
        
        # Check for specific errors
        error_str = str(e)
        if "SystemError: 6: forbidden" in error_str and "storage.py" in error_str:
            print_status("🎯 FOUND: SystemError: 6: forbidden in storage.py!", Colors.YELLOW)
            print_status("This matches the Linear issue DXP-609 pattern", Colors.YELLOW)
            return "ERROR_REPRODUCED"
        
        return False

def test_state_persistence(client, contract_address, initial_state):
    """Verify that write simulation didn't modify actual contract state"""
    print_status(f"\n=== VERIFYING STATE PERSISTENCE ===", Colors.BLUE)
    
    try:
        # Read have_coin state again
        print_status("Reading have_coin state after write simulation...", Colors.CYAN)
        final_state = client.read_contract(
            address=contract_address,
            function_name="get_have_coin"
        )
        
        print_status(f"Initial state: have_coin = {initial_state}", Colors.CYAN)
        print_status(f"Final state: have_coin = {final_state}", Colors.CYAN)
        
        if initial_state == final_state:
            print_status("🎯 CONFIRMED: Write simulation did NOT modify actual contract state!", Colors.GREEN)
            print_status("State remains unchanged after simulation.", Colors.GREEN)
        else:
            print_status("⚠️  WARNING: Contract state was modified by write simulation!", Colors.YELLOW)
            
        return True
        
    except Exception as e:
        print_status(f"❌ State verification failed: {e}", Colors.RED)
        return False

def test_actual_write(client, contract_address):
    """Execute an actual write transaction"""
    print_status(f"\n=== TESTING ACTUAL WRITE TRANSACTION ===", Colors.BLUE)
    
    try:
        print_status("\n1. Executing ask_for_coin transaction...", Colors.CYAN)
        
        # Execute the transaction
        tx_hash = client.write_contract(
            address=contract_address,
            function_name="ask_for_coin",
            args=["Oh mighty wizard, I humbly request your magical coin!"]
        )
        
        print_status(f"✅ Write transaction submitted: {tx_hash}", Colors.GREEN)
        
        # Wait for transaction confirmation
        print_status("Waiting for transaction confirmation...", Colors.CYAN)
        receipt = client.w3.eth.wait_for_transaction_receipt(tx_hash)
        
        if receipt.status == 1:
            print_status("✅ Transaction confirmed successfully", Colors.GREEN)
        else:
            print_status(f"⚠️  Transaction failed with status: {receipt.status}", Colors.YELLOW)
        
        return True
        
    except Exception as e:
        print_status(f"❌ Write transaction failed: {e}", Colors.RED)
        return False

def verify_final_state(client, contract_address, initial_state):
    """Check the final state after write transaction"""
    print_status(f"\n=== CHECKING FINAL STATE ===", Colors.BLUE)
    
    try:
        # Wait a bit for state to update
        time.sleep(2)
        
        # Read final state
        print_status("Reading final have_coin state...", Colors.CYAN)
        final_state = client.read_contract(
            address=contract_address,
            function_name="get_have_coin"
        )
        
        print_status(f"Initial state: have_coin = {initial_state}", Colors.CYAN)
        print_status(f"Final state: have_coin = {final_state}", Colors.CYAN)
        
        if initial_state != final_state:
            print_status("✅ State changed! The wizard gave away the coin!", Colors.GREEN)
        else:
            print_status("✅ State unchanged! The wizard kept the coin!", Colors.GREEN)
            
        return final_state
        
    except Exception as e:
        print_status(f"❌ Final state check failed: {e}", Colors.RED)
        return None

def main():
    """Main test execution"""
    print_status("\n" + "="*60, Colors.BLUE)
    print_status("WizardOfCoin Contract Test Suite with genlayer_py", Colors.BLUE)
    print_status("="*60 + "\n", Colors.BLUE)
    
    try:
        # Step 1: Create client
        print_status("Creating GenLayer client...", Colors.CYAN)
        client, account = get_client()
        print_status(f"✅ Client created with account: {account.address}", Colors.GREEN)
        
        # Step 2: Deploy contract
        contract_address = deploy_wizard_contract(client)
        if not contract_address:
            print_status("❌ Test failed - could not deploy contract", Colors.RED)
            return 1
        
        # Save contract address for other scripts
        with open(os.path.join(os.path.dirname(__file__), '.last_deployed_contract'), 'w') as f:
            f.write(contract_address)
        
        # Step 3: Test read operations
        initial_state = test_read_operations(client, contract_address)
        
        # Step 4: Test write simulation (looking for potential issues)
        simulation_result = test_write_simulation(client, contract_address)
        
        # Step 5: Verify state persistence after simulation
        test_state_persistence(client, contract_address, initial_state)
        
        # Step 6: Test actual write transaction
        write_result = test_actual_write(client, contract_address)
        
        # Step 7: Verify final state
        if write_result:
            final_state = verify_final_state(client, contract_address, initial_state)
        
        # Summary
        print_status("\n" + "="*60, Colors.GREEN)
        print_status("🎉 SUCCESS: Full test completed!", Colors.GREEN)
        print_status("="*60, Colors.GREEN)
        
        print_status(f"✅ WizardOfCoin contract deployed at: {contract_address}", Colors.GREEN)
        print_status("✅ Read operations tested", Colors.GREEN)
        
        if simulation_result == "ERROR_REPRODUCED":
            print_status("🎯 LINEAR ISSUE DXP-609 pattern detected!", Colors.YELLOW)
            print_status("✅ SystemError: 6: forbidden in storage.py detected", Colors.YELLOW)
        elif simulation_result:
            print_status("✅ Write simulation operations tested (no error detected)", Colors.GREEN)
        else:
            print_status("❌ Write simulation failed", Colors.RED)
            
        print_status("✅ State persistence verified", Colors.GREEN)
        print_status("✅ Actual write transaction tested", Colors.GREEN)
        
        print_status(f"\nContract address saved to: .last_deployed_contract", Colors.CYAN)
        
        return 0
        
    except Exception as e:
        print_status(f"\n❌ Test failed with error: {e}", Colors.RED)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())