const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

// Function to get the balance of an account
async function getAccountBalance(address) {
  const balance = await hre.network.provider.send("eth_getBalance", [address, "latest"]);
  return parseInt(balance, 16);
}

// Gets the storage of a contract by iterating slot by slot up to a maximum.
async function getContractStorage(address, maxSlots = 50) {
  console.log(`[${new Date().toISOString()}] Reading storage for ${address} (up to ${maxSlots} slots)...`);
  const storage = {};

  // Read only the first N slots, one by one
  for (let i = 0; i < maxSlots; i++) {
    try {
      const slotHex = "0x" + i.toString(16).padStart(64, "0");
      // Use eth_getStorageAt correctly with "latest" block
      const value = await hre.network.provider.send("eth_getStorageAt", [address, slotHex, "latest"]);

      // Only store non-zero values to save space
      if (value && value !== "0x0000000000000000000000000000000000000000000000000000000000000000") {
        storage[slotHex] = value;
      }
    } catch (error) {
      console.error(`[${new Date().toISOString()}] Error reading slot ${i} for ${address}: ${error.message}`);
    }
  }

  console.log(`[${new Date().toISOString()}] Captured ${Object.keys(storage).length} non-zero slots for ${address}`);
  return storage;
}

// Specifically captures the ghostContracts mapping from ConsensusMain
async function captureGhostContractsMapping(consensusMainAddress, ghostAddresses) {
  console.log(`[${new Date().toISOString()}] Capturing ghostContracts mapping for ${ghostAddresses.length} ghosts...`);
  const storage = {};

  // The ghostContracts mapping slot is 1 (according to contract code)
  const mappingSlot = 1;

  for (const ghostAddress of ghostAddresses) {
    try {
      // Calculate specific slot for this ghost in the mapping
      // keccak256(abi.encodePacked(ghostAddress, mappingSlot))
      const ethers = hre.ethers;
      const paddedAddress = ethers.utils.hexZeroPad(ghostAddress, 32);
      const paddedSlot = ethers.utils.hexZeroPad(ethers.BigNumber.from(mappingSlot).toHexString(), 32);
      const encodedData = ethers.utils.concat([paddedAddress, paddedSlot]);
      const slotKey = ethers.utils.keccak256(encodedData);

      // Get the value at this slot
      const value = await hre.network.provider.send("eth_getStorageAt", [
        consensusMainAddress,
        slotKey,
        "latest"
      ]);

      // Only store if true (1)
      if (value && value !== "0x0000000000000000000000000000000000000000000000000000000000000000") {
        storage[slotKey] = value;
        console.log(`[${new Date().toISOString()}] Ghost ${ghostAddress} registered, value: ${value}`);
      }
    } catch (error) {
      console.error(`[${new Date().toISOString()}] Error capturing ghost ${ghostAddress}: ${error.message}`);
    }
  }

  return storage;
}

async function main() {
  console.log(`[${new Date().toISOString()}] Starting snapshot process...`);
  await new Promise(resolve => setTimeout(resolve, 1000));

  const blockNumber = await hre.network.provider.send("eth_blockNumber");
  const blockNumberDec = parseInt(blockNumber, 16);
  const latestBlock = await hre.network.provider.send("eth_getBlockByNumber", ["latest", true]);
  const accounts = await hre.network.provider.send("eth_accounts");
  const chainId = await hre.network.provider.send("eth_chainId");
  const gasPrice = await hre.network.provider.send("eth_gasPrice");

  const snapshotId = await hre.network.provider.send("evm_snapshot");
  console.log(`[${new Date().toISOString()}] Snapshot taken with ID: ${snapshotId}`);

  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const deploymentDir = path.join(__dirname, "../deployments/genlayer_network");
  const deploymentFiles = fs.readdirSync(deploymentDir).filter(file => file.endsWith(".json"));

  // Phase 1: Collect basic information for all contracts
  const deployments = {};
  let ghostAddresses = [];

  for (const file of deploymentFiles) {
    const contractName = file.replace(".json", "");
    const deploymentPath = path.join(deploymentDir, file);
    const deploymentData = JSON.parse(fs.readFileSync(deploymentPath, "utf8"));
    const runtimeCode = await hre.network.provider.send("eth_getCode", [deploymentData.address, "latest"]);
    if (runtimeCode === "0x") continue;

    deployments[contractName] = {
      address: deploymentData.address,
      abi: deploymentData.abi,
      bytecode: deploymentData.bytecode,
      deployedBytecode: deploymentData.deployedBytecode,
      runtimeCode
    };

    // If it's GhostFactory, try to get ghost contracts
    if (contractName === "GhostFactory" || contractName === "ghostFactory") {
      try {
        console.log(`[${new Date().toISOString()}] Attempting to get ghosts from ${contractName}...`);
        const ghostFactory = new hre.ethers.Contract(
          deploymentData.address,
          deploymentData.abi,
          hre.ethers.provider
        );

        // Try different methods to get ghosts
        if (ghostFactory.interface.hasFunction("getGhosts")) {
          ghostAddresses = await ghostFactory.getGhosts();
          console.log(`[${new Date().toISOString()}] Retrieved ${ghostAddresses.length} ghosts using getGhosts()`);
        } else if (ghostFactory.interface.hasFunction("getAllGhosts")) {
          ghostAddresses = await ghostFactory.getAllGhosts();
          console.log(`[${new Date().toISOString()}] Retrieved ${ghostAddresses.length} ghosts using getAllGhosts()`);
        } else if (ghostFactory.interface.hasFunction("latestGhost")) {
          const latestGhost = await ghostFactory.latestGhost();
          if (latestGhost && latestGhost !== "0x0000000000000000000000000000000000000000") {
            ghostAddresses = [latestGhost];
            console.log(`[${new Date().toISOString()}] Retrieved latest ghost: ${latestGhost}`);
          }
        }
      } catch (error) {
        console.error(`[${new Date().toISOString()}] Error getting ghosts: ${error.message}`);
      }
    }
  }

  // If no ghost contracts found, look for them in ConsensusMain events
  if (ghostAddresses.length === 0 && deployments.ConsensusMain) {
    try {
      console.log(`[${new Date().toISOString()}] Looking for ghosts in ConsensusMain events...`);
      const consensusMain = new hre.ethers.Contract(
        deployments.ConsensusMain.address,
        deployments.ConsensusMain.abi,
        hre.ethers.provider
      );

      const filter = consensusMain.filters.NewTransaction();
      const events = await consensusMain.queryFilter(filter);
      const recipientSet = new Set();

      for (const event of events) {
        if (event.args && event.args.recipient && event.args.recipient !== "0x0000000000000000000000000000000000000000") {
          recipientSet.add(event.args.recipient);
        }
      }

      ghostAddresses = [...recipientSet];
      console.log(`[${new Date().toISOString()}] Found ${ghostAddresses.length} ghosts in events`);
    } catch (error) {
      console.error(`[${new Date().toISOString()}] Error looking for ghosts in events: ${error.message}`);
    }
  }

  // Phase 2: Capture storage for key contracts
  // For ConsensusMain, capture both initial slots and ghostContracts mapping
  if (deployments.ConsensusMain) {
    console.log(`[${new Date().toISOString()}] Capturing basic storage for ConsensusMain...`);
    const baseStorage = await getContractStorage(deployments.ConsensusMain.address, 10);

    // If we have ghost contracts, also capture their slots in the mapping
    let ghostStorage = {};
    if (ghostAddresses.length > 0) {
      ghostStorage = await captureGhostContractsMapping(
        deployments.ConsensusMain.address,
        ghostAddresses
      );
    }

    // Combine both storages
    deployments.ConsensusMain.storage = { ...baseStorage, ...ghostStorage };
    console.log(`[${new Date().toISOString()}] Total slots captured for ConsensusMain: ${Object.keys(deployments.ConsensusMain.storage).length}`);
  }

  // Capture basic storage for other key contracts
  const keyContracts = ["GhostFactory", "ConsensusManager", "Transactions", "Queues"];
  for (const contractName of keyContracts) {
    if (deployments[contractName]) {
      console.log(`[${new Date().toISOString()}] Capturing basic storage for ${contractName}...`);
      deployments[contractName].storage = await getContractStorage(deployments[contractName].address, 10);
    }
  }

  // Capture balances and nonces
  const accountBalances = {};
  const accountNonces = {};
  for (const account of accounts) {
    accountBalances[account] = await getAccountBalance(account);
    const nonce = await hre.network.provider.send("eth_getTransactionCount", [account, "latest"]);
    accountNonces[account] = parseInt(nonce, 16);
  }

  const snapshotData = {
    id: snapshotId,
    timestamp: Date.now(),
    network: hre.network.name,
    chainId: parseInt(chainId, 16),
    blockNumber: blockNumberDec,
    latestBlock: {
      number: blockNumberDec,
      hash: latestBlock.hash,
      timestamp: parseInt(latestBlock.timestamp, 16),
      transactions: latestBlock.transactions.length,
      gasUsed: parseInt(latestBlock.gasUsed, 16),
      gasLimit: parseInt(latestBlock.gasLimit, 16)
    },
    gasPrice: parseInt(gasPrice, 16),
    accounts: {
      addresses: accounts,
      balances: accountBalances,
      nonces: accountNonces
    },
    deployments
  };

  const snapshotsDir = path.join(__dirname, "../snapshots");
  if (!fs.existsSync(snapshotsDir)) fs.mkdirSync(snapshotsDir, { recursive: true });

  const snapshotPath = path.join(snapshotsDir, `snapshot-${timestamp}.json`);
  fs.writeFileSync(snapshotPath, JSON.stringify(snapshotData, null, 2));
  fs.writeFileSync(
    path.join(snapshotsDir, "latest.json"),
    JSON.stringify({ ...snapshotData, file: `snapshot-${timestamp}.json` }, null, 2)
  );
  console.log(`[${new Date().toISOString()}] Snapshot saved at block ${blockNumberDec}`);
}

main()
  .then(() => process.exit(0))
  .catch(e => {
    console.error(e);
    process.exit(1);
  });
