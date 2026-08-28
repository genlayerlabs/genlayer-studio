const { expect } = require("chai");
const { deployments, ethers } = require("hardhat");

describe("ConsensusMain internal message bridge", function () {
    let consensusMain;
    let ghostFactory;
    let queues;
	let transactions;
    let owner;
    let recipient;
    let deploymentTxId;

    beforeEach(async function () {
        await deployments.fixture();
        [owner] = await ethers.getSigners();
        consensusMain = await ethers.getContractAt(
            "ConsensusMain",
            (await deployments.get("ConsensusMain")).address,
        );
        ghostFactory = await ethers.getContractAt(
            "GhostFactory",
            (await deployments.get("GhostFactory")).address,
        );
        queues = await ethers.getContractAt(
            "Queues",
            (await deployments.get("Queues")).address,
        );
		transactions = await ethers.getContractAt(
			"Transactions",
			(await deployments.get("Transactions")).address,
		);

        const deploymentTx = await consensusMain.addTransaction(
            owner.address,
            ethers.ZeroAddress,
            5,
            0,
            "0x01",
        );
        const deploymentReceipt = await deploymentTx.wait();
        const creationEvent = deploymentReceipt.logs
            .map((log) => {
                try {
                    return consensusMain.interface.parseLog(log);
                } catch (_error) {
                    return null;
                }
            })
            .find((event) => event && event.name === "NewTransaction");
        deploymentTxId = creationEvent.args.txId;
        recipient = await ghostFactory.latestGhost();
    });

    it("keeps duplicate-recipient children distinct and replays a phase once", async function () {
        const parentTxId = ethers.keccak256(ethers.toUtf8Bytes("parent"));
        const messages = [
            {
                sender: recipient,
                recipient,
				saltNonce: 0,
                data: "0x0102",
            },
            {
                sender: recipient,
                recipient,
				saltNonce: 0,
                data: "0x0304",
            },
        ];
        const issuedBefore = await queues.getIssuedTxCount(recipient);

        await consensusMain.emitTransactionAccepted(parentTxId, messages);

        const firstIds = await consensusMain.getInternalMessageTxIds(
            parentTxId,
            true,
            messages,
        );
        expect(firstIds).to.have.length(2);
        expect(firstIds[0]).to.not.equal(firstIds[1]);
        expect(await queues.getIssuedTxCount(recipient)).to.equal(
            issuedBefore + 2n,
        );

        await consensusMain.emitTransactionAccepted(parentTxId, messages);

        expect(
            await consensusMain.getInternalMessageTxIds(
                parentTxId,
                true,
                messages,
            ),
        ).to.deep.equal(firstIds);
        expect(await queues.getIssuedTxCount(recipient)).to.equal(
            issuedBefore + 2n,
        );
    });

	it("accepts the v0.6 fee-aware tuple selectors and authors salted recipients", async function () {
		const feeAwareInterface = new ethers.Interface([
			"function addTransaction((address sender,address recipient,uint256 numOfInitialValidators,uint256 maxRotations,uint256 validUntil,uint256 saltNonce,uint256 userValue,(uint256 leaderTimeunitsAllocation,uint256 validatorTimeunitsAllocation,uint256 appealRounds,uint256 executionBudgetPerRound,uint256 executionConsumed,uint256 totalMessageFees,uint256[] rotations,uint256 maxPriceGenPerTimeUnit,uint256 storageFeeMaxGasPrice,uint256 receiptFeeMaxGasPrice) feesDistribution,bytes txCalldata,(uint8 messageType,bool onAcceptance,uint256 parentIndex,address recipient,bytes32 callKey,uint256 budget,bytes feeParams)[] messageAllocations) params) payable",
			"function deploySalted((address sender,address recipient,uint256 numOfInitialValidators,uint256 maxRotations,uint256 validUntil,uint256 saltNonce,uint256 userValue,(uint256 leaderTimeunitsAllocation,uint256 validatorTimeunitsAllocation,uint256 appealRounds,uint256 executionBudgetPerRound,uint256 executionConsumed,uint256 totalMessageFees,uint256[] rotations,uint256 maxPriceGenPerTimeUnit,uint256 storageFeeMaxGasPrice,uint256 receiptFeeMaxGasPrice) feesDistribution,bytes txCalldata,(uint8 messageType,bool onAcceptance,uint256 parentIndex,address recipient,bytes32 callKey,uint256 budget,bytes feeParams)[] messageAllocations) params) payable",
		]);
		const feesDistribution = {
			leaderTimeunitsAllocation: 1,
			validatorTimeunitsAllocation: 1,
			appealRounds: 0,
			executionBudgetPerRound: 0,
			executionConsumed: 0,
			totalMessageFees: 0,
			rotations: [0],
			maxPriceGenPerTimeUnit: 1,
			storageFeeMaxGasPrice: 0,
			receiptFeeMaxGasPrice: 0,
		};
		const baseParams = {
			sender: owner.address,
			recipient,
			numOfInitialValidators: 5,
			maxRotations: 0,
			validUntil: 0,
			saltNonce: 0,
			userValue: 0,
			feesDistribution,
			txCalldata: "0x0102",
			messageAllocations: [],
		};
		const issuedBefore = await queues.getIssuedTxCount(recipient);

		await owner.sendTransaction({
			to: await consensusMain.getAddress(),
			data: feeAwareInterface.encodeFunctionData("addTransaction", [
				baseParams,
			]),
		});
		expect(await queues.getIssuedTxCount(recipient)).to.equal(
			issuedBefore + 1n,
		);

		const saltedTx = await owner.sendTransaction({
			to: await consensusMain.getAddress(),
			data: feeAwareInterface.encodeFunctionData("deploySalted", [
				{
					...baseParams,
					recipient: ethers.ZeroAddress,
					saltNonce: 777n,
				},
			]),
		});
		const saltedReceipt = await saltedTx.wait();
		const newTransaction = saltedReceipt.logs
			.map((log) => {
				try {
					return consensusMain.interface.parseLog(log);
				} catch (_error) {
					return null;
				}
			})
			.find((event) => event && event.name === "NewTransaction");

		expect(newTransaction.args.recipient).to.not.equal(ethers.ZeroAddress);
		expect(newTransaction.args.recipient).to.equal(
			await ghostFactory.latestGhost(),
		);
		expect(
			await consensusMain.ghostContracts(newTransaction.args.recipient),
		).to.equal(true);
	});

    it("seats a distinct decision payload once without duplicating its retries", async function () {
        const parentTxId = ethers.keccak256(ethers.toUtf8Bytes("drift-parent"));
        const messages = [
            {
                sender: recipient,
                recipient,
				saltNonce: 0,
                data: "0x0102",
            },
        ];
        await consensusMain.emitTransactionFinalized(parentTxId, messages);
        const drifted = [{ ...messages[0], data: "0x9999" }];
        const issuedBeforeDrift = await queues.getIssuedTxCount(recipient);

        await consensusMain.emitTransactionFinalized(parentTxId, drifted);
        const driftedIds = await consensusMain.getInternalMessageTxIds(
            parentTxId,
            false,
            drifted,
        );
        expect(driftedIds).to.have.length(1);
        expect(await queues.getIssuedTxCount(recipient)).to.equal(
            issuedBeforeDrift + 1n,
        );

        await consensusMain.emitTransactionFinalized(parentTxId, drifted);
        expect(
            await consensusMain.getInternalMessageTxIds(
                parentTxId,
                false,
                drifted,
            ),
        ).to.deep.equal(driftedIds);
        expect(await queues.getIssuedTxCount(recipient)).to.equal(
            issuedBeforeDrift + 1n,
        );
    });

	for (const saltNonce of [0n, 42n]) {
		it(`lets the helper authoritatively deploy and durably replay a child ghost with salt ${saltNonce}`, async function () {
			const parentTxId = ethers.keccak256(
				ethers.toUtf8Bytes(`deployment-parent-${saltNonce}`),
			);
			const messages = [
				{
					sender: recipient,
					recipient: ethers.ZeroAddress,
					saltNonce,
					data: "0xdeadbeef",
				},
			];

			await consensusMain.emitTransactionAccepted(parentTxId, messages);
			const childIds = await consensusMain.getInternalMessageTxIds(
				parentTxId,
				true,
				messages,
			);
			const childRecipients =
				await consensusMain.getInternalMessageRecipients(
					parentTxId,
					true,
					messages,
				);

			expect(childIds).to.have.length(1);
			expect(childRecipients).to.have.length(1);
			expect(childRecipients[0]).to.not.equal(ethers.ZeroAddress);
			expect(await consensusMain.ghostContracts(childRecipients[0])).to.equal(
				true,
			);
			expect(
				await transactions.getTransactionRecipient(childIds[0]),
			).to.equal(childRecipients[0]);
			expect(await queues.getIssuedTxCount(childRecipients[0])).to.equal(1n);

			await consensusMain.emitTransactionAccepted(parentTxId, messages);
			expect(
				await consensusMain.getInternalMessageRecipients(
					parentTxId,
					true,
					messages,
				),
			).to.deep.equal(childRecipients);
			expect(await queues.getIssuedTxCount(childRecipients[0])).to.equal(1n);
		});
	}

	it("keeps a successful salted deployment when a colliding sibling is skipped", async function () {
		const parentTxId = ethers.keccak256(
			ethers.toUtf8Bytes("deployment-collision-parent"),
		);
		const messages = [0, 1].map((index) => ({
			sender: recipient,
			recipient: ethers.ZeroAddress,
			saltNonce: 99n,
			data: index === 0 ? "0x0102" : "0x0304",
		}));

		await consensusMain.emitTransactionAccepted(parentTxId, messages);
		const childIds = await consensusMain.getInternalMessageTxIds(
			parentTxId,
			true,
			messages,
		);
		const childRecipients =
			await consensusMain.getInternalMessageRecipients(
				parentTxId,
				true,
				messages,
			);

		expect(childIds[0]).to.not.equal(ethers.ZeroHash);
		expect(childIds[1]).to.equal(ethers.ZeroHash);
		expect(childRecipients[0]).to.not.equal(ethers.ZeroAddress);
		expect(childRecipients[1]).to.equal(ethers.ZeroAddress);
		expect(await queues.getIssuedTxCount(childRecipients[0])).to.equal(1n);

		await consensusMain.emitTransactionAccepted(parentTxId, messages);
		expect(await queues.getIssuedTxCount(childRecipients[0])).to.equal(1n);
	});

    it("indexes finalized history by recipient issuance order", async function () {
        await consensusMain.cancelTransaction(deploymentTxId);

        expect(await queues.getFinalizedCount(recipient)).to.equal(1n);
        expect(await queues.getFinalizedTxId(recipient, 0)).to.equal(
            deploymentTxId,
        );
    });

    it("skips an unregistered internal recipient without stranding valid siblings", async function () {
        const parentTxId = ethers.keccak256(
            ethers.toUtf8Bytes("non-ghost-parent"),
        );
        const messages = [
                {
                    sender: recipient,
                    recipient: owner.address,
					saltNonce: 0,
                    data: "0x0102",
                },
                {
                    sender: recipient,
                    recipient,
                    saltNonce: 0,
                    data: "0x0304",
                },
            ];
        const issuedBefore = await queues.getIssuedTxCount(recipient);

        await consensusMain.emitTransactionAccepted(parentTxId, messages);

        const childIds = await consensusMain.getInternalMessageTxIds(
            parentTxId,
            true,
            messages,
        );
        const childRecipients =
            await consensusMain.getInternalMessageRecipients(
                parentTxId,
                true,
                messages,
            );

        expect(childIds[0]).to.equal(ethers.ZeroHash);
        expect(childIds[1]).to.not.equal(ethers.ZeroHash);
        expect(childRecipients).to.deep.equal([owner.address, recipient]);
        expect(await consensusMain.ghostContracts(owner.address)).to.equal(false);
        expect(await queues.getIssuedTxCount(recipient)).to.equal(
            issuedBefore + 1n,
        );

        await consensusMain.emitTransactionAccepted(parentTxId, messages);
        expect(await queues.getIssuedTxCount(recipient)).to.equal(
            issuedBefore + 1n,
        );
    });
});
