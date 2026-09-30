const { expect } = require("chai");
const { deployments, ethers } = require("hardhat");

describe("GhostFactory salted deployment namespace", function () {
	let ghostFactory;
	let consensusMain;
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
		consensusMain = await ethers.getContractAt(
			"ConsensusMain",
			(await deployments.get("ConsensusMain")).address,
		);
	});

	it("lets unrelated authenticated senders reuse the same salt", async function () {
		await ghostFactory.connect(owner).setGenConsensus(owner.address);
		await ghostFactory.createGhost(firstUser.address, 42);
		const firstGhost = await ghostFactory.latestGhost();

		await ghostFactory.createGhost(secondUser.address, 42);
		const secondGhost = await ghostFactory.latestGhost();

		expect(secondGhost).to.not.equal(firstGhost);
		expect(await ghostFactory.isGhost(firstGhost)).to.equal(true);
		expect(await ghostFactory.isGhost(secondGhost)).to.equal(true);
	});

	it("threads each internal parent namespace through ConsensusMain", async function () {
		const parentTxId = ethers.keccak256(
			ethers.toUtf8Bytes("cross-parent-salt-namespace"),
		);
		const messages = [firstUser, secondUser].map((user, index) => ({
			sender: user.address,
			recipient: ethers.ZeroAddress,
			saltNonce: 42,
			data: index === 0 ? "0x0102" : "0x0304",
		}));

		await consensusMain.emitTransactionAccepted(parentTxId, messages);
		const childIds = await consensusMain.getInternalMessageTxIds(
			parentTxId,
			true,
			messages,
		);
		const recipients = await consensusMain.getInternalMessageRecipients(
			parentTxId,
			true,
			messages,
		);

		expect(childIds).to.have.length(2);
		expect(childIds[0]).to.not.equal(ethers.ZeroHash);
		expect(childIds[1]).to.not.equal(ethers.ZeroHash);
		expect(recipients[0]).to.not.equal(recipients[1]);
		expect(await ghostFactory.isGhost(recipients[0])).to.equal(true);
		expect(await ghostFactory.isGhost(recipients[1])).to.equal(true);
	});
});
