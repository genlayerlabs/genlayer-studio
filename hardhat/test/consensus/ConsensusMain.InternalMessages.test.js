const { expect } = require("chai");
const { deployments, ethers } = require("hardhat");

describe("ConsensusMain internal message bridge", function () {
    let consensusMain;
    let ghostFactory;
    let queues;
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
                data: "0x0102",
            },
            {
                sender: recipient,
                recipient,
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

    it("seats a distinct decision payload once without duplicating its retries", async function () {
        const parentTxId = ethers.keccak256(ethers.toUtf8Bytes("drift-parent"));
        const messages = [
            {
                sender: recipient,
                recipient,
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

    it("indexes finalized history by recipient issuance order", async function () {
        await consensusMain.cancelTransaction(deploymentTxId);

        expect(await queues.getFinalizedCount(recipient)).to.equal(1n);
        expect(await queues.getFinalizedTxId(recipient, 0)).to.equal(
            deploymentTxId,
        );
    });
});
