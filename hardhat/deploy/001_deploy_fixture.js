const fs = require("fs-extra");
const path = require("path");
const { ethers } = require("hardhat");

module.exports = async ({ getNamedAccounts, deployments, ethers }) => {
  const { deploy, execute, log } = deployments;
  const { deployer } = await getNamedAccounts();
  const signers = await ethers.getSigners();

  // Define validators as in your deployFixture
  const validator1 = signers[1].address;
  const validator2 = signers[2].address;
  const validator3 = signers[3].address;
  const validator4 = signers[4].address;
  const validator5 = signers[5].address;

  log("Deploying GhostContract...");
  const ghostContractDeployment = await deploy("GhostContract", {
    from: deployer,
    log: true,
  });

  // Deploy libraries first
  log("Deploying ArrayUtils...");
  const arrayUtilsDeployment = await deploy("ArrayUtils", {
    from: deployer,
    log: true,
  });

  log("Deploying RandomnessUtils...");
  const randomnessUtilsDeployment = await deploy("RandomnessUtils", {
    from: deployer,
    log: true,
  });

  // Deploy core contracts
  log("Deploying Transactions...");
  const genTransactionsDeployment = await deploy("Transactions", {
    from: deployer,
    libraries: {
      ArrayUtils: arrayUtilsDeployment.address,
    },
    log: true,
  });

  log("Deploying ConsensusManager...");
  const genManagerDeployment = await deploy("ConsensusManager", {
    from: deployer,
    log: true,
  });

  log("Deploying GhostFactory...");
  const ghostFactoryDeployment = await deploy("GhostFactory", {
    from: deployer,
    log: true,
  });

  // Initialize GhostFactory
  if (ghostFactoryDeployment.newlyDeployed) {
    log("Initializing GhostFactory...");
    await execute(
      "GhostFactory",
      { from: deployer },
      "initialize"
    );
  } else {
    log("Skipping GhostFactory.initialize() because it's already deployed.");
  }

  log("Deploying GhostBlueprint...");
  const ghostBlueprintDeployment = await deploy("GhostBlueprint", {
    from: deployer,
    log: true,
  });

  if (ghostBlueprintDeployment.newlyDeployed) {
    log("Initializing GhostBlueprint...");
    await execute(
      "GhostBlueprint",
      { from: deployer },
      "initialize",
      deployer  // owner
    );
  } else {
    log("Skipping GhostBlueprint.initialize() because it's already deployed.");
  }

  log("Setting GhostBlueprint in GhostFactory...");
  try {
    await execute(
      "GhostFactory",
      { from: deployer, gasLimit: 5000000 },
      "setGhostBlueprint",
      ghostBlueprintDeployment.address
    );
    log("GhostBlueprint set successfully");

    log("Deploying new beacon proxy...");
    await execute(
      "GhostFactory",
      { from: deployer, gasLimit: 5000000 },
      "deployNewBeaconProxy"
    );
    log("Beacon proxy deployed successfully");
  } catch (err) {
    log(`Error in GhostFactory setup: ${err.message}`);
  }

  log("Deploying MockGenStaking...");
  const genStakingDeployment = await deploy("MockGenStaking", {
    from: deployer,
    log: true,
  });

  log("Deploying Queues...");
  const genQueueDeployment = await deploy("Queues", {
    from: deployer,
    log: true,
  });

  log("Deploying Messages...");
  const messagesDeployment = await deploy("Messages", {
    from: deployer,
    log: true,
  });

  log("Deploying FeeManager...");
  const feeManagerDeployment = await deploy("FeeManager", {
    from: deployer,
    log: true,
  });

  log("Deploying Utils...");
  const utilsDeployment = await deploy("Utils", {
    from: deployer,
    log: true,
  });

  log("Deploying Rounds...");
  const roundsDeployment = await deploy("Rounds", {
    from: deployer,
    libraries: {
      ArrayUtils: arrayUtilsDeployment.address,
      RandomnessUtils: randomnessUtilsDeployment.address,
    },
    args: [
      genStakingDeployment.address,
      feeManagerDeployment.address,
      utilsDeployment.address,
    ],
    log: true,
  });

  log("Deploying Voting...");
  const votingDeployment = await deploy("Voting", {
    from: deployer,
    log: true,
  });

  log("Deploying Idleness...");
  const idlenessDeployment = await deploy("Idleness", {
    from: deployer,
    libraries: {
      ArrayUtils: arrayUtilsDeployment.address,
    },
    args: [
      genTransactionsDeployment.address,
      genStakingDeployment.address,
      utilsDeployment.address,
    ],
    log: true,
  });

  log("Deploying ConsensusMain...");
  const consensusMainDeployment = await deploy("ConsensusMain", {
    from: deployer,
    log: true,
  });

  // Initialize ConsensusMain
  if (consensusMainDeployment.newlyDeployed) {
    log("Initializing ConsensusMain...");
    await execute(
      "ConsensusMain",
      { from: deployer },
      "initialize"
    );
  } else {
    log("Skipping ConsensusMain.initialize() because it's already deployed.");
  }

  const consensusMainAddress = consensusMainDeployment.address;

  // Initialize Transactions with ConsensusMain
  if (genTransactionsDeployment.newlyDeployed) {
    log("Initializing Transactions...");
    await execute(
      "Transactions",
      { from: deployer },
      "initialize",
      consensusMainAddress
    );
  } else {
    log("Skipping Transactions.initialize() because it's already deployed.");
  }

  // Initialize Queues with ConsensusMain
  if (genQueueDeployment.newlyDeployed) {
    log("Initializing Queues...");
    await execute(
      "Queues",
      { from: deployer },
      "initialize",
      consensusMainAddress
    );
  } else {
    log("Skipping Queues.initialize() because it's already deployed.");
  }

  // Initialize Messages
  if (messagesDeployment.newlyDeployed) {
    log("Initializing Messages...");
    await execute(
      "Messages",
      { from: deployer },
      "initialize"
    );
  } else {
    log("Skipping Messages.initialize() because it's already deployed.");
  }

  // Set external contracts in ConsensusMain
  log("Setting external contracts in ConsensusMain...");
  try {
    await execute(
      "ConsensusMain",
      { from: deployer, gasLimit: 5000000 },
      "setExternalContracts",
      ghostFactoryDeployment.address,
      genManagerDeployment.address,
      genTransactionsDeployment.address,
      genQueueDeployment.address,
      genStakingDeployment.address,
      messagesDeployment.address,
      idlenessDeployment.address
    );
    log("External contracts set in ConsensusMain");
  } catch (err) {
    log(`Error setting external contracts in ConsensusMain: ${err.message}`);
  }

  // Set external contracts in Transactions
  log("Setting external contracts in Transactions...");
  try {
    await execute(
      "Transactions",
      { from: deployer, gasLimit: 5000000 },
      "setExternalContracts",
      consensusMainDeployment.address,
      genStakingDeployment.address,
      roundsDeployment.address,
      votingDeployment.address,
      idlenessDeployment.address,
      utilsDeployment.address
    );
    log("External contracts set in Transactions");
  } catch (err) {
    log(`Error setting external contracts in Transactions: ${err.message}`);
  }

  // Deploy and initialize ConsensusData
  log("Deploying ConsensusData...");
  const consensusDataDeployment = await deploy("ConsensusData", {
    from: deployer,
    log: true,
  });

  if (consensusDataDeployment.newlyDeployed) {
    log("Initializing ConsensusData...");
    try {
      await execute(
        "ConsensusData",
        { from: deployer, gasLimit: 5000000 },
        "initialize",
        consensusMainDeployment.address,
        genTransactionsDeployment.address,
        genQueueDeployment.address
      );
      log("ConsensusData initialized");
    } catch (err) {
      log(`Error initializing ConsensusData: ${err.message}`);
    }
  } else {
    log("Skipping ConsensusData.initialize() because it's already deployed.");
  }

  // Set GenConsensus in GhostFactory
  log("Setting GenConsensus in GhostFactory...");
  try {
    await execute(
      "GhostFactory",
      { from: deployer, gasLimit: 3000000 },
      "setGenConsensus",
      consensusMainDeployment.address
    );
    log("GenConsensus set in GhostFactory");
  } catch (err) {
    log(`Error setting GenConsensus in GhostFactory: ${err.message}`);
  }

  // Set GhostManager in GhostFactory
  log("Setting GhostManager in GhostFactory...");
  try {
    await execute(
      "GhostFactory",
      { from: deployer, gasLimit: 3000000 },
      "setGhostManager",
      consensusMainDeployment.address
    );
    log("GhostManager set in GhostFactory");
  } catch (err) {
    log(`Error setting GhostManager in GhostFactory: ${err.message}`);
  }

  // Set GenConsensus in Messages
  log("Setting GenConsensus in Messages...");
  try {
    await execute(
      "Messages",
      { from: deployer, gasLimit: 3000000 },
      "setGenConsensus",
      consensusMainDeployment.address
    );
    log("GenConsensus set in Messages");
  } catch (err) {
    log(`Error setting GenConsensus in Messages: ${err.message}`);
  }

  // Set GenTransactions in Messages
  log("Setting GenTransactions in Messages...");
  try {
    await execute(
      "Messages",
      { from: deployer, gasLimit: 3000000 },
      "setGenTransactions",
      genTransactionsDeployment.address
    );
    log("GenTransactions set in Messages");
  } catch (err) {
    log(`Error setting GenTransactions in Messages: ${err.message}`);
  }

  // Add validators
  log("Adding validators...");
  try {
    await execute(
      "MockGenStaking",
      { from: deployer, gasLimit: 3000000 },
      "addValidators",
      [validator1, validator2, validator3, validator4, validator5]
    );
    log("Validators added successfully");
  } catch (err) {
    log(`Error adding validators: ${err.message}`);
  }

  log("All contracts deployed and configured!\n");
};

module.exports.tags = ["All"];