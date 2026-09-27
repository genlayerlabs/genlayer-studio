const { expect } = require("chai");
const { deployments, ethers } = require("hardhat");

// The Transactions lifecycle entry points activateTransaction,
// finalizeTransaction and submitAppeal must only be reachable through the
// consensus contract. Their siblings in the same contract
// (proposeTransactionReceipt, cancelTransaction, rotateLeader) already carry
// onlyGenConsensus; these three did not, so any external account could pick
// the activating committee seed, force a transaction to Finalized, or open an
// appeal. See issue #1767.
describe("Transactions onlyGenConsensus gate", function () {
    let transactions;
    let consensusMain;
    let outsider;
    let txId;

    // Errors.CallerNotConsensus() takes no arguments, so its revert data is
    // exactly its 4-byte selector. The repo does not depend on
    // hardhat-chai-matchers, so the revert is asserted against the selector.
    let callerNotConsensusSelector;

    async function expectCallerNotConsensus(promise) {
        let revertData = null;
        try {
            await promise;
        } catch (error) {
            // Hardhat reports the revert either as error.data (a string or a
            // { data, ... } object) or nested under info.error.data.
            const raw = error.data ?? error.info?.error?.data ?? null;
            revertData =
                typeof raw === "string"
                    ? raw
                    : (raw?.data ?? raw?.reason?.Revert ?? null);
        }
        expect(revertData, "expected the call to revert").to.not.equal(null);
        expect(revertData).to.equal(callerNotConsensusSelector);
    }

    beforeEach(async function () {
        await deployments.fixture();
        const signers = await ethers.getSigners();
        [outsider] = signers;

        transactions = await ethers.getContractAt(
            "Transactions",
            (await deployments.get("Transactions")).address,
        );
        consensusMain = await ethers.getContractAt(
            "ConsensusMain",
            (await deployments.get("ConsensusMain")).address,
        );
        callerNotConsensusSelector =
            transactions.interface.getError("CallerNotConsensus").selector;

        txId = ethers.keccak256(ethers.toUtf8Bytes("only-gen-consensus-tx"));
    });

    it("stores the consensus contract as the gate target", async function () {
        // Transactions is initialized with the ConsensusMain address, so the
        // gate resolves to a real address rather than the zero address.
        const externalContracts = await transactions.contracts();
        expect(externalContracts.genConsensus).to.equal(
            await consensusMain.getAddress(),
        );
    });

    it("rejects activateTransaction from a non-consensus caller", async function () {
        await expectCallerNotConsensus(
            transactions
                .connect(outsider)
                .activateTransaction(txId, outsider.address, ethers.ZeroHash),
        );
    });

    it("rejects finalizeTransaction from a non-consensus caller", async function () {
        await expectCallerNotConsensus(
            transactions.connect(outsider).finalizeTransaction(txId),
        );
    });

    it("rejects submitAppeal from a non-consensus caller", async function () {
        await expectCallerNotConsensus(
            transactions.connect(outsider).submitAppeal(txId, 0),
        );
    });

    it("rejects finalizeTransaction via the raw selector from a non-consensus caller", async function () {
        // A hand-encoded call bypassing the typed helper still hits the modifier.
        const data = transactions.interface.encodeFunctionData(
            "finalizeTransaction",
            [txId],
        );
        await expectCallerNotConsensus(
            outsider.sendTransaction({
                to: await transactions.getAddress(),
                data,
            }),
        );
    });

    it("keeps the already-gated sibling cancelTransaction gated", async function () {
        await expectCallerNotConsensus(
            transactions.connect(outsider).cancelTransaction(txId, outsider.address),
        );
    });
});
