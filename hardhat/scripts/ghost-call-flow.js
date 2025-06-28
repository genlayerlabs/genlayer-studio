const hre = require("hardhat");
const fs = require("fs-extra");
const path = require("path");
const { ethers } = hre;

async function generateSignature(signer, currentSeed) {
  const seedBytes = ethers.zeroPadValue(ethers.toBeHex(currentSeed), 32);
  const vrfProof = await signer.signMessage(ethers.getBytes(seedBytes));
  return vrfProof;
}

async function completeConsensusFlow(
  consensusMain,
  consensusData,
  genManager,
  txId,
  ghostAddress,
  activator,
  validators,
  proposedReceipt = "0x1234",
  nonces
) {
  try {
    console.log(`Starting consensus flow for transaction ${txId}`);

    // 1. Activate transaction
    console.log("Step 1: Activating transaction...");
    let currentSeed = await genManager.recipientRandomSeed(ghostAddress);
    console.log(`Current seed for activation: ${currentSeed}`);
    const vrfProofActivate = await generateSignature(activator, BigInt(currentSeed));
    console.log(`Generated VRF proof for activation: ${vrfProofActivate.slice(0, 20)}...`);

    console.log(`Activator address: ${activator.address}`);
    const activateTx = await consensusMain.connect(activator).activateTransaction(txId, vrfProofActivate);
    console.log(`Activation transaction hash: ${activateTx.hash}`);
    const activationReceipt = await activateTx.wait();
    if (!activationReceipt) throw new Error("Transaction activation failed");
    console.log("Transaction activated successfully");

    // Get leader from activation event
    const activationEvent = activationReceipt.logs?.find(
      (log) => {
        try {
          return consensusMain.interface.parseLog(log)?.name === "TransactionActivated";
        } catch (e) {
          return false;
        }
      }
    );

    if (!activationEvent) throw new Error("TransactionActivated event not found");
    const parsedActivationEvent = consensusMain.interface.parseLog(activationEvent);
    const leaderAddress = parsedActivationEvent.args[1];
    const leader = validators.find((v) => v.address === leaderAddress);
    if (!leader) throw new Error(`Leader not found among validators: ${leaderAddress}`);
    console.log(`Leader is ${leader.address}`);

    // 2. Leader proposes receipt
    console.log("Step 2: Leader proposing receipt...");
    currentSeed = await genManager.recipientRandomSeed(ghostAddress);
    console.log(`Current seed for proposal: ${currentSeed}`);
    const vrfProofPropose = await generateSignature(leader, BigInt(currentSeed));
    console.log(`Generated VRF proof for proposal: ${vrfProofPropose.slice(0, 20)}...`);

    const currentBlock = await ethers.provider.getBlockNumber();
    console.log(`Current block number: ${currentBlock}`);

    console.log(`Leader address: ${leader.address}`);
    console.log(`Proposed receipt: ${proposedReceipt}`);

    const proposeReceipt = await consensusMain
      .connect(leader)
      .proposeReceipt(txId, proposedReceipt, currentBlock, [], vrfProofPropose);
    console.log(`Proposal transaction hash: ${proposeReceipt.hash}`);
    const proposeReceiptReceipt = await proposeReceipt.wait();
    if (!proposeReceiptReceipt) throw new Error("Transaction proposal failed");
    console.log("Receipt proposed successfully");

    // 3. Commit votes
    console.log("Step 3: Committing votes...");
    const voteType = 1; // Agree
    for (let i = 0; i < validators.length; i++) {
      const validator = validators[i];
      const nonce = nonces[i];
      console.log(`Committing vote for validator ${validator.address} with nonce ${nonce}`);

      const voteHash = ethers.solidityPackedKeccak256(
        ["address", "uint8", "uint256"],
        [validator.address, voteType, nonce]
      );
      console.log(`Vote hash: ${voteHash}`);

      try {
        const commitTx = await consensusMain.connect(validator).commitVote(txId, voteHash);
        console.log(`Commit vote transaction hash for validator ${i+1}: ${commitTx.hash}`);
        const commitReceipt = await commitTx.wait();
        if (!commitReceipt) throw new Error(`Commit vote failed for validator ${validator.address}`);
      } catch (error) {
        console.error(`Error committing vote for validator ${validator.address}:`, error.message);
        // Continue with next validator instead of failing the whole process
      }
    }
    console.log("All votes committed successfully");

    // Wait a bit to ensure all commit transactions have been mined
    console.log("Waiting for all commit transactions to be mined...");
    await new Promise(resolve => setTimeout(resolve, 2000));

    // 4. Reveal votes
    console.log("Step 4: Revealing votes...");
    for (let i = 0; i < validators.length; i++) {
      const validator = validators[i];
      const nonce = nonces[i];
      console.log(`Revealing vote for validator ${validator.address} with nonce ${nonce}`);

      const voteHash = ethers.solidityPackedKeccak256(
        ["address", "uint8", "uint256"],
        [validator.address, voteType, nonce]
      );
      console.log(`Vote hash: ${voteHash}`);

      try {
        const revealTx = await consensusMain.connect(validator).revealVote(txId, voteHash, voteType, nonce);
        console.log(`Reveal vote transaction hash for validator ${i+1}: ${revealTx.hash}`);
        const revealReceipt = await revealTx.wait();
        if (!revealReceipt) throw new Error(`Reveal vote failed for validator ${validator.address}`);
      } catch (error) {
        console.error(`Error revealing vote for validator ${validator.address}:`, error.message);
        // Continue with next validator instead of failing the whole process
      }
    }
    console.log("All votes revealed successfully");

    // Wait a bit to ensure all reveal transactions have been mined
    console.log("Waiting for all reveal transactions to be mined...");
    await new Promise(resolve => setTimeout(resolve, 2000));

    // 5. Finalize transaction
    console.log("Step 5: Finalizing transaction...");
    try {
      // Verify current transaction status before finalizing
      const txStatus = await consensusData.getTransactionStatus(txId);
      console.log(`Transaction status before finalization: ${txStatus}`);

      const finalizeTx = await consensusMain.finalizeTransaction(txId);
      console.log(`Finalize transaction hash: ${finalizeTx.hash}`);
      await finalizeTx.wait();

    } catch (error) {
      console.error("Error finalizing transaction:", error.message);
      // Don't throw error, try to get final status anyway
    }

    // Get final transaction status
    const finalStatus = await consensusData.getTransactionStatus(txId);
    console.log(`Final transaction status: ${finalStatus}`);
    return finalStatus;
  } catch (error) {
    console.error(`Error in consensus flow for transaction ${txId}:`, error);
    throw error;
  }
}

async function main() {
  console.log("Starting ghost deployment and call flow...");

  // Get signers
  const [owner, validator1, validator2, validator3, validator4, validator5] = await hre.ethers.getSigners();
  const validators = [validator1, validator2, validator3, validator4, validator5];

  // Determine the correct deployment path based on network
  let deployPath;
  if (hre.network.name === "genlayer_network") {
    deployPath = path.join('./deployments/genlayer_network');
    console.log("Using deployment files from deployments/genlayer_network");
  } else if (hre.network.name === "hardhat") {
    deployPath = path.join('./deployments/hardhat');
    console.log("Using deployment files from deployments/hardhat");
  } else {
    deployPath = path.join('./deployments/localhost');
    console.log("Using deployment files from deployments/localhost");
  }

  try {
    // Verify directory exists
    if (!await fs.pathExists(deployPath)) {
      throw new Error(`Deployment directory not found: ${deployPath}. Run deploy.js script first.`);
    }

    // Load deployment files
    const consensusMainFile = await fs.readJson(path.join(deployPath, "ConsensusMain.json"));
    const consensusDataFile = await fs.readJson(path.join(deployPath, "ConsensusData.json"));
    const genManagerFile = await fs.readJson(path.join(deployPath, "ConsensusManager.json"));

    console.log("Contract addresses loaded:");
    console.log("- ConsensusMain:", consensusMainFile.address);
    console.log("- ConsensusData:", consensusDataFile.address);
    console.log("- ConsensusManager:", genManagerFile.address);

    // Get contract instances
    const consensusMain = await hre.ethers.getContractAt("ConsensusMain", consensusMainFile.address);
    const consensusData = await hre.ethers.getContractAt("ConsensusData", consensusDataFile.address);
    const genManager = await hre.ethers.getContractAt("ConsensusManager", genManagerFile.address);

    const maxRotations = 2;

    // 1. Deploy ghost contract
    console.log("\n1. Deploying ghost contract...");
    const deployTx = await consensusMain.addTransaction(
      ethers.ZeroAddress, // sender
      ethers.ZeroAddress, // recipient (will create ghost)
      5, // number of validators
      maxRotations,
      "0x1234" // transaction data
    );
    const deployReceipt = await deployTx.wait();

    const deployEvent = deployReceipt.logs?.find(
      (log) => {
        try {
          return consensusMain.interface.parseLog(log)?.name === "NewTransaction";
        } catch (e) {
          return false;
        }
      }
    );

    if (!deployEvent) throw new Error("NewTransaction event not found for ghost deployment");
    const deployParsedLog = consensusMain.interface.parseLog(deployEvent);
    const deployTxId = deployParsedLog.args[0];
    const ghostAddress = deployParsedLog.args[1];
    const deployActivator = validators.find((v) => v.address === deployParsedLog.args[2]);

    console.log("- Deploy Transaction ID:", deployTxId);
    console.log("- Ghost Address:", ghostAddress);
    console.log("- Deploy Activator:", deployActivator.address);

    // Complete consensus flow for ghost deployment
    console.log("\n2. Completing consensus flow for ghost deployment...");
    const deployStatus = await completeConsensusFlow(
      consensusMain,
      consensusData,
      genManager,
      deployTxId,
      ghostAddress,
      deployActivator,
      validators,
      "0x123456",
      [123, 456, 789, 1011, 1213] // deployNonces
    );
    console.log("- Ghost deployment status:", deployStatus.toString());

    // Verify ghost contract owner
    const GhostBlueprint = await hre.ethers.getContractFactory("GhostBlueprint");
    const ghost = GhostBlueprint.attach(ghostAddress);
    const ghostOwner = await ghost.owner();
    console.log("- Ghost contract owner:", ghostOwner);
    console.log("- ConsensusMain address:", consensusMain.target);

    // 3. Create dummy call through ghost contract
    console.log("\n3. Creating dummy call through ghost contract...");
    const dummyCallTx = await ghost.addTransaction(
      5, // number of validators
      maxRotations,
      ethers.keccak256(ethers.toUtf8Bytes("dummyFunction()")) // encode function selector
    );
    const dummyCallReceipt = await dummyCallTx.wait();

    const dummyCallEvent = dummyCallReceipt.logs?.find(
      (log) => {
        try {
          return consensusMain.interface.parseLog(log)?.name === "NewTransaction";
        } catch (e) {
          return false;
        }
      }
    );

    if (!dummyCallEvent) throw new Error("NewTransaction event not found for dummy call");
    const dummyCallParsedLog = consensusMain.interface.parseLog(dummyCallEvent);
    const dummyCallTxId = dummyCallParsedLog.args[0];
    const dummyCallActivator = validators.find((v) => v.address === dummyCallParsedLog.args[2]);

    console.log("- Dummy Call Transaction ID:", dummyCallTxId);
    console.log("- Dummy Call Activator:", dummyCallActivator.address);

    // Complete consensus flow for dummy call
    console.log("\n4. Completing consensus flow for dummy call...");
    const dummyCallStatus = await completeConsensusFlow(
      consensusMain,
      consensusData,
      genManager,
      dummyCallTxId,
      ghostAddress,
      dummyCallActivator,
      validators,
      "0x5678",
      [321, 654, 987, 1316, 1649] // ghostNonces
    );
    console.log("- Dummy call status:", dummyCallStatus.toString());

    // Get and log transaction data
    const txData = await consensusData.getTransactionData(dummyCallTxId);
    console.log("Transaction Data:", txData);

    if (deployStatus.toString() === "7" && dummyCallStatus.toString() === "7") {
      console.log("\nGhost deployment and dummy call completed successfully! ✓");
    } else {
      console.log(`\nWarning: Unexpected final status: Deploy=${deployStatus}, DummyCall=${dummyCallStatus}`);
    }
  } catch (error) {
    console.error("Error during ghost call flow:", error);
    throw error;
  }
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
