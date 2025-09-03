#!/usr/bin/env node

/**
 * Deploy WizardOfCoin contract using genlayer-js SDK
 * This script uses the official SDK to properly encode the deployment transaction
 */

const fs = require('fs');
const path = require('path');
const { createWalletClient, createPublicClient, http } = require('viem');
const { privateKeyToAccount } = require('viem/accounts');
const { GenLayerClient } = require('@genlayerlabs/genlayer-js');

// Configuration
const PRIVATE_KEY = process.env.PRIVATE_KEY || '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';
const RPC_URL = process.env.BASE_URL || 'http://localhost:4000/api';
const CONTRACT_PATH = path.join(__dirname, '../../examples/contracts/wizard_of_coin.py');

// GenLayer configuration
const genlayerConfig = {
  id: 61999,
  name: 'GenLayer',
  rpcUrl: RPC_URL,
  consensusMainContract: {
    address: '0xb7278a61aa25c888815afc32ad3cc52ff24fe575',
  },
  defaultConsensusMaxRotations: 3,
};

async function deployContract() {
  try {
    // Load contract code
    const contractCode = fs.readFileSync(CONTRACT_PATH, 'utf8');
    console.log('Contract code loaded:', contractCode.length, 'bytes');

    // Create account from private key
    const account = privateKeyToAccount(PRIVATE_KEY);
    console.log('Deploying from account:', account.address);

    // Create GenLayer client
    const client = new GenLayerClient({
      chain: genlayerConfig,
      transport: http(RPC_URL),
      account: account,
    });

    console.log('Deploying WizardOfCoin contract...');
    
    // Deploy the contract
    const txHash = await client.deployContract({
      code: contractCode,
      args: [true], // Constructor argument: have_coin = true
      consensusMaxRotations: 3,
    });

    console.log('Transaction submitted:', txHash);
    
    // Wait for transaction receipt
    const publicClient = createPublicClient({
      chain: genlayerConfig,
      transport: http(RPC_URL),
    });

    console.log('Waiting for transaction receipt...');
    const receipt = await publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: 60000,
    });

    console.log('Transaction receipt:', receipt);
    
    // Extract contract address from logs
    if (receipt.logs && receipt.logs.length > 0) {
      const log = receipt.logs[0];
      if (log.topics && log.topics[2]) {
        const contractAddress = '0x' + log.topics[2].slice(-40);
        console.log('Contract deployed at:', contractAddress);
        
        // Save contract address for later use
        fs.writeFileSync(path.join(__dirname, '.last_deployed_contract'), contractAddress);
        fs.writeFileSync(path.join(__dirname, '.last_deployment_tx'), txHash);
        
        return contractAddress;
      }
    }
    
    console.log('Could not extract contract address from receipt');
    return null;

  } catch (error) {
    console.error('Deployment error:', error);
    throw error;
  }
}

// Run if called directly
if (require.main === module) {
  deployContract()
    .then(address => {
      if (address) {
        console.log('✅ Deployment successful!');
        console.log('Contract address:', address);
        process.exit(0);
      } else {
        console.log('❌ Deployment failed');
        process.exit(1);
      }
    })
    .catch(error => {
      console.error('Fatal error:', error);
      process.exit(1);
    });
}

module.exports = { deployContract };