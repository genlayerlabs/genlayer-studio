const { expect } = require("chai");
const { deployments, ethers } = require("hardhat");

describe("GhostFactory salted deployment namespace", function () {
	let ghostFactory;
	let owner;
	let firstUser;
	let secondUser;

	beforeEach(async function () {
		await deployments.fixture();
		[owner, firstUser, secondUser] = await ethers.getSigners();
		ghostFactory = await ethers.getContractAt(
			"GhostFactory",
			(await deployments.get("GhostFactory")).address,
		);
		await ghostFactory.connect(owner).setGenConsensus(owner.address);
	});

	it("lets unrelated authenticated senders reuse the same salt", async function () {
		await ghostFactory.createGhost(firstUser.address, 42);
		const firstGhost = await ghostFactory.latestGhost();

		await ghostFactory.createGhost(secondUser.address, 42);
		const secondGhost = await ghostFactory.latestGhost();

		expect(secondGhost).to.not.equal(firstGhost);
		expect(await ghostFactory.isGhost(firstGhost)).to.equal(true);
		expect(await ghostFactory.isGhost(secondGhost)).to.equal(true);
	});
});
