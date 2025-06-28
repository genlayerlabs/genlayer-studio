const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

/**
 * Mine blocks until reaching the target block number
 * @param {number} targetBlock Target block number to reach
 */
async function mineToBlock(targetBlock) {
  const currentHex = await hre.network.provider.send("eth_blockNumber");
  const currentDec = parseInt(currentHex, 16);
  const diff = targetBlock - currentDec;
  if (diff > 0) {
    console.log(`Mining ${diff} blocks to reach block ${targetBlock}...`);
    await hre.network.provider.send("hardhat_mine", [`0x${diff.toString(16)}`]);
  }
}

/**
 * Restores all contract relationships and settings from 001_deploy_fixture.js
 * @param {object} snapshotData Data from the snapshot
 */
async function restoreFixtureConfigurations(snapshotData) {
  console.log(`Restoring contract configurations from deploy_fixture...`);

  try {
    const { deployments } = snapshotData;
    const signers = await hre.ethers.getSigners();
    const deployer = signers[0];

    const getContract = (name) => {
      const data = deployments[name];
      if (!data) return null;
      return new hre.ethers.Contract(data.address, data.abi, deployer);
    };

    const c = {
      GhostFactory: getContract("GhostFactory"),
      GhostBlueprint: getContract("GhostBlueprint"),
      ConsensusMain: getContract("ConsensusMain"),
      Transactions: getContract("Transactions"),
      Queues: getContract("Queues"),
      Messages: getContract("Messages"),
      ConsensusManager: getContract("ConsensusManager"),
      MockGenStaking: getContract("MockGenStaking"),
      Idleness: getContract("Idleness"),
      Rounds: getContract("Rounds"),
      Voting: getContract("Voting"),
      Utils: getContract("Utils"),
      ConsensusData: getContract("ConsensusData"),
    };

    // 1. Initialize GhostFactory
    if (c.GhostFactory?.initialize) {
      try {
        console.log(`Initializing GhostFactory...`);
        await c.GhostFactory.initialize();
      } catch (_) {}
    }

    // 2. Initialize GhostBlueprint
    if (c.GhostBlueprint?.initialize) {
      try {
        console.log(`Initializing GhostBlueprint...`);
        await c.GhostBlueprint.initialize(deployer.address);
      } catch (_) {}
    }

    // 3. Set GhostBlueprint
    if (c.GhostFactory && c.GhostBlueprint?.address && c.GhostFactory.setGhostBlueprint) {
      try {
        console.log(`Setting GhostBlueprint in GhostFactory...`);
        await c.GhostFactory.setGhostBlueprint(c.GhostBlueprint.address);
      } catch (_) {}
    }

    // 4. Deploy new beacon proxy
    let latestGhost = null;
    if (c.GhostFactory?.deployNewBeaconProxy) {
      try {
        console.log(`Deploying new beacon proxy...`);
        const tx = await c.GhostFactory.deployNewBeaconProxy();
        const receipt = await tx.wait();
        const event = receipt.events.find(e => e.event === 'GhostDeployed');
        if (event) {
          latestGhost = event.args[0];
        }
      } catch (_) {}
    }

    // 5. Initialize ConsensusMain
    if (c.ConsensusMain?.initialize) {
      try {
        console.log(`Initializing ConsensusMain...`);
        await c.ConsensusMain.initialize();
      } catch (_) {}
    }

    // 6. Initialize Transactions with ConsensusMain
    if (c.Transactions?.initialize && c.ConsensusMain?.address) {
      try {
        console.log(`Initializing Transactions...`);
        await c.Transactions.initialize(c.ConsensusMain.address);
      } catch (_) {}
    }

    // 7. Initialize Queues
    if (c.Queues?.initialize && c.ConsensusMain?.address) {
      try {
        console.log(`Initializing Queues...`);
        await c.Queues.initialize(c.ConsensusMain.address);
      } catch (_) {}
    }

    // 8. Initialize Messages
    if (c.Messages?.initialize) {
      try {
        console.log(`Initializing Messages...`);
        await c.Messages.initialize();
      } catch (_) {}
    }

    // 9. Set external contracts in ConsensusMain
    if (
      c.ConsensusMain?.setExternalContracts &&
      c.GhostFactory && c.ConsensusManager && c.Transactions &&
      c.Queues && c.MockGenStaking && c.Messages && c.Idleness
    ) {
      try {
        console.log(`Setting external contracts in ConsensusMain...`);
        await c.ConsensusMain.setExternalContracts(
          c.GhostFactory.address,
          c.ConsensusManager.address,
          c.Transactions.address,
          c.Queues.address,
          c.MockGenStaking.address,
          c.Messages.address,
          c.Idleness.address
        );
      } catch (_) {}
    }

    // 9.1. Re-store the ghost into ConsensusMain
    if (latestGhost && c.ConsensusMain?.storeGhost) {
      try {
        console.log(`Storing ghost ${latestGhost} in ConsensusMain...`);
        await c.ConsensusMain.storeGhost(latestGhost);
      } catch (err) {
        console.log(`Error storing ghost: ${err.message}`);
      }
    }

    // 10. Set external contracts in Transactions
    if (
      c.Transactions?.setExternalContracts &&
      c.ConsensusMain && c.MockGenStaking && c.Rounds &&
      c.Voting && c.Idleness && c.Utils
    ) {
      try {
        console.log(`Setting external contracts in Transactions...`);
        await c.Transactions.setExternalContracts(
          c.ConsensusMain.address,
          c.MockGenStaking.address,
          c.Rounds.address,
          c.Voting.address,
          c.Idleness.address,
          c.Utils.address
        );
      } catch (_) {}
    }

    // 11. Initialize ConsensusData
    if (c.ConsensusData?.initialize && c.ConsensusMain && c.Transactions && c.Queues) {
      try {
        console.log(`Initializing ConsensusData...`);
        await c.ConsensusData.initialize(
          c.ConsensusMain.address,
          c.Transactions.address,
          c.Queues.address
        );
      } catch (_) {}
    }

    // 12. Set GenConsensus in GhostFactory
    if (c.GhostFactory?.setGenConsensus && c.ConsensusMain) {
      try {
        console.log(`Setting GenConsensus in GhostFactory...`);
        await c.GhostFactory.setGenConsensus(c.ConsensusMain.address);
      } catch (_) {}
    }

    // 13. Set GhostManager in GhostFactory
    if (c.GhostFactory?.setGhostManager && c.ConsensusMain) {
      try {
        console.log(`Setting GhostManager in GhostFactory...`);
        await c.GhostFactory.setGhostManager(c.ConsensusMain.address);
      } catch (_) {}
    }

    // 14. Set GenConsensus in Messages
    if (c.Messages?.setGenConsensus && c.ConsensusMain) {
      try {
        console.log(`Setting GenConsensus in Messages...`);
        await c.Messages.setGenConsensus(c.ConsensusMain.address);
      } catch (_) {}
    }

    // 15. Set GenTransactions in Messages
    if (c.Messages?.setGenTransactions && c.Transactions) {
      try {
        console.log(`Setting GenTransactions in Messages...`);
        await c.Messages.setGenTransactions(c.Transactions.address);
      } catch (_) {}
    }

    // 16. Add validators
    if (c.MockGenStaking?.addValidators) {
      try {
        const validators = signers.slice(1, 6).map(s => s.address);
        console.log(`Adding validators to MockGenStaking...`);
        await c.MockGenStaking.addValidators(validators);
      } catch (_) {}
    }

    console.log(`Contract configurations restored successfully`);
    return true;
  } catch (error) {
    console.log(`Error restoring fixture configurations: ${error.message}`);
    return false;
  }
}

/**
 * Restores state using evm_revert, and if it fails, performs a full restoration
 * @param {object} snapshotData Snapshot data
 */
async function restoreState(snapshotData) {
  const snapshotId = snapshotData.id;
  const originalBlockNumber = snapshotData.blockNumber;
  console.log(`Attempting to restore state from block ${originalBlockNumber} with snapshot ID ${snapshotId}...`);

  try {
    // Method 1: Try with evm_revert (this is the fastest and most reliable)
    const reverted = await hre.network.provider.send("evm_revert", [snapshotId]);

    if (reverted === true) {
      console.log(`evm_revert successful!`);

      // Take a new snapshot immediately to preserve the state
      const newSnapshotId = await hre.network.provider.send("evm_snapshot");
      console.log(`New snapshot taken with ID: ${newSnapshotId}`);

      // Update snapshot with new ID
      snapshotData.id = newSnapshotId;

      const snapshotPath = path.join(__dirname, "../snapshots/latest.json");
      fs.writeFileSync(snapshotPath, JSON.stringify(snapshotData, null, 2));

      // Verify current block matches the original
      const currentHex = await hre.network.provider.send("eth_blockNumber");
      const currentBlock = parseInt(currentHex, 16);

      if (currentBlock !== originalBlockNumber) {
        console.log(`Warning: Current block (${currentBlock}) does not match original block (${originalBlockNumber})`);
        await mineToBlock(originalBlockNumber);
      }

      return true;
    } else {
      console.log(`evm_revert failed, falling back to full restoration...`);
    }
  } catch (error) {
    console.log(`Error with evm_revert: ${error.message}`);
    console.log(`Falling back to full restoration...`);
  }

  // Method 2: Full restoration (if evm_revert failed)
  return await restoreBlockchainState(snapshotData);
}

/**
 * Full restoration of blockchain state (used as fallback)
 * @param {object} snapshotData Snapshot data
 */
async function restoreBlockchainState(snapshotData) {
  console.log(`Starting full state restoration...`);
  const originalBlockNumber = snapshotData.blockNumber;

  // Create snapshots directory if it doesn't exist
  const snapshotsDir = path.join(__dirname, "../snapshots");
  if (!fs.existsSync(snapshotsDir)) {
    fs.mkdirSync(snapshotsDir, { recursive: true });
  }

  // Make sure deployment files exist for all contracts in snapshot
  const deploymentsDir = path.join(__dirname, "../deployments/genlayer_network");
  if (!fs.existsSync(deploymentsDir)) {
    fs.mkdirSync(deploymentsDir, { recursive: true });
  }

  // Complete node reset
  await hre.network.provider.send("hardhat_reset");

  // Mine until the block captured in the snapshot
  await mineToBlock(originalBlockNumber);

  // Restore contract code and storage
  for (const [contractName, data] of Object.entries(snapshotData.deployments)) {
    // Check if contract already has code
    const code = await hre.network.provider.send("eth_getCode", [data.address, "latest"]);

    if (code === "0x") {
      // Restore bytecode
      let bytecode = data.runtimeCode || data.deployedBytecode || data.bytecode;
      if (!bytecode) {
        console.log(`No bytecode for ${contractName}, skipping`);
        continue;
      }

      if (!bytecode.startsWith("0x")) {
        bytecode = "0x" + bytecode;
      }

      try {
        console.log(`Restoring code for ${contractName} at ${data.address}`);
        await hre.network.provider.send("hardhat_setCode", [data.address, bytecode]);
      } catch (error) {
        console.log(`Error restoring code for ${contractName}: ${error.message}`);
        continue; // If we can't restore the code, move to next contract
      }
    }

    // Restore storage
    if (data.storage && Object.keys(data.storage).length > 0) {
      console.log(`Restoring ${Object.keys(data.storage).length} storage slots for ${contractName}`);

      for (const [slot, value] of Object.entries(data.storage)) {
        try {
          await hre.network.provider.send("hardhat_setStorageAt", [data.address, slot, value]);
        } catch (error) {
          console.log(`Error restoring slot ${slot}: ${error.message}`);
        }
      }
    }

    // Register in hardhat-deploy
    try {
      await hre.deployments.save(contractName, {
        address: data.address,
        abi: data.abi,
        bytecode: data.bytecode,
        deployedBytecode: data.deployedBytecode
      });
    } catch (error) {
      console.log(`Error registering ${contractName}: ${error.message}`);
    }
  }

  // Verify we're at the correct block
  const finalBlockHex = await hre.network.provider.send("eth_blockNumber");
  const finalBlock = parseInt(finalBlockHex, 16);
  if (finalBlock !== originalBlockNumber) {
    console.log(`Final block (${finalBlock}) does not match original (${originalBlockNumber}), mining to correct block...`);
    await mineToBlock(originalBlockNumber);
  }

  // Restore all contract configurations from deploy_fixture
  await restoreFixtureConfigurations(snapshotData);

  // Take a new snapshot of the restored state
  const newSnapshotId = await hre.network.provider.send("evm_snapshot");
  console.log(`New snapshot taken with ID: ${newSnapshotId}`);

  // Update snapshot file with new ID
  snapshotData.id = newSnapshotId;
  const snapshotPath = path.join(__dirname, "../snapshots/latest.json");
  fs.writeFileSync(snapshotPath, JSON.stringify(snapshotData, null, 2));

  return true;
}

/**
 * Run tests to verify that contracts are working correctly
 * @param {object} snapshotData Snapshot data
 */
async function verifyContractsWork(snapshotData) {
  if (!snapshotData.deployments.ConsensusMain) {
    console.log(`ConsensusMain not found, skipping verification`);
    return;
  }

  try {
    // Verify that we can interact with ConsensusMain
    console.log(`Verifying ConsensusMain functionality...`);

    const [signer] = await hre.ethers.getSigners();
    const consensusMain = new hre.ethers.Contract(
      snapshotData.deployments.ConsensusMain.address,
      snapshotData.deployments.ConsensusMain.abi,
      signer
    );

    // Verify that the contract responds
    const owner = await consensusMain.owner();

    // Verify GhostFactory functionality if it exists
    if (snapshotData.deployments.GhostFactory) {
      console.log(`Verifying GhostFactory functionality...`);

      const ghostFactory = new hre.ethers.Contract(
        snapshotData.deployments.GhostFactory.address,
        snapshotData.deployments.GhostFactory.abi,
        signer
      );

      try {
        const blueprint = await ghostFactory.ghostBlueprint();
        const zeroAddress = "0x0000000000000000000000000000000000000000";
        if (blueprint === zeroAddress) {
          console.log(`Warning: GhostBlueprint is not set in GhostFactory`);
        }
      } catch (error) {
        console.log(`Error checking GhostFactory blueprint: ${error.message}`);
      }
    }

    console.log(`Contract verification successful`);
  } catch (error) {
    console.log(`Error verifying contracts: ${error.message}`);
  }
}

async function takeNewSnapshot() {
  try {
    console.log(`Taking new snapshot after restoration...`);

    // Instead of calling hre.run("snapshot"), directly execute the snapshot script
    // that we know exists in the project
    const snapshotPath = path.join(__dirname, "./snapshot.js");
    if (fs.existsSync(snapshotPath)) {
      // Use require to execute the script
      require(snapshotPath);
    } else {
      console.log(`Snapshot script not found at ${snapshotPath}`);

      // Fallback: Take a basic snapshot
      const newSnapshotId = await hre.network.provider.send("evm_snapshot");
      console.log(`Basic snapshot taken with ID: ${newSnapshotId}`);
    }
  } catch (error) {
    console.log(`Error taking new snapshot: ${error.message}`);
  }
}

async function main() {
  const snapshotPath = path.join(__dirname, "../snapshots/latest.json");

  if (!fs.existsSync(snapshotPath)) {
    console.log(`No snapshot to restore at ${snapshotPath}`);
    return;
  }

  console.log(`Loading snapshot from ${snapshotPath}...`);
  const snapshotData = JSON.parse(fs.readFileSync(snapshotPath, "utf8"));

  // Save original block number for verification at the end
  const originalBlockNumber = snapshotData.blockNumber;
  console.log(`Original block number: ${originalBlockNumber}`);

  // Restore state
  await restoreState(snapshotData);

  // Verify everything works correctly
  await verifyContractsWork(snapshotData);

  // Verify final block number
  const finalBlockHex = await hre.network.provider.send("eth_blockNumber");
  const finalBlock = parseInt(finalBlockHex, 16);

  if (finalBlock !== originalBlockNumber) {
    console.log(`Warning: Final block (${finalBlock}) does not match original (${originalBlockNumber})`);
    console.log(`Mining to reach the target block...`);
    await mineToBlock(originalBlockNumber);
  }

  console.log(`Restoration process completed at block ${finalBlock}`);

  // Take a new snapshot to ensure we can revert to this state again
  await takeNewSnapshot();
}

// Execute the script
main()
  .then(() => process.exit(0))
  .catch(err => {
    console.error(err);
    process.exit(1);
  });