const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("FeeManager.calculateRoundFees — normal-round rotation indexing", function () {
  it("uses the normal-round ordinal for later raw consensus rounds", async function () {
    const [consensus] = await ethers.getSigners();
    const feeManager = await ethers.deployContract("FeeManager");
    await feeManager.waitForDeployment();
    await feeManager.initialize(consensus.address);

    const txId = ethers.id("later-normal-round-rotation-quote");
    const feesDistribution = {
      leaderTimeout: 100,
      validatorsTimeout: 200,
      appealRounds: 2,
      rollupStorageFee: 0,
      rollupGenVMFee: 0,
      totalMessageFees: 0,
      rotations: [0, 1, 2],
    };

    await feeManager.topUpFees(
      txId,
      feesDistribution,
      0,
      false,
      consensus.address
    );

    // Raw round 4 is normal-round ordinal 2. With rotations[2] = 2 and
    // 23 validators, the quote covers 3 attempts at 100 + 23 * 200 each.
    expect(
      await feeManager.calculateRoundFees(txId, feesDistribution, 23, 4)
    ).to.equal(3n * (100n + 23n * 200n));
  });
});
