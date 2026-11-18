const { expect } = require("chai");
const { deployments, ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

// ITransactions.VoteType. TransactionStatus is parsed out of the Solidity
// source instead, see readTransactionStatusEnum.
const VoteType = {
    NotVoted: 0,
    Agree: 1,
    Disagree: 2,
    Timeout: 3,
    DeterministicViolation: 4,
};
let Status;

// solidity's ABI JSON drops enum members and this ethers version has no
// interface.getEnum, so the ordinals are read from the interface source. That
// keeps the assertions below from going stale if the enum is ever renumbered.
function readTransactionStatusEnum() {
    const source = fs.readFileSync(
        path.join(
            __dirname,
            "..",
            "..",
            "contracts",
            "v2_contracts",
            "transactions",
            "interfaces",
            "ITransactions.sol",
        ),
        "utf8",
    );
    const body = source.match(/enum\s+TransactionStatus\s*{([^}]*)}/)[1];
    return body
        .split(",")
        .map((entry) => entry.replace(/\/\/.*$/gm, "").trim())
        .filter((entry) => entry.length > 0)
        .reduce((acc, name, index) => {
            acc[name] = index;
            return acc;
        }, {});
}

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
    let consensusManager;
    let outsider;
    let txId;
    let signers;

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

    // The deploy fixture registers signers 1..5 as validators and owns their
    // private keys, so the validator a transaction picks out of the staking
    // contract is a signer this test can actually sign with.
    function signerFor(address) {
        const signer = signers.find((s) => s.address === address);
        expect(signer, `no signer for validator ${address}`).to.not.equal(
            undefined,
        );
        return signer;
    }

    // RandomnessUtils.updateRandomSeed recovers the signer from an
    // Ethereum-signed message hash over the current seed, so the VRF proof is
    // just an ordinary personal_sign over those 32 seed bytes.
    async function signSeed(who, recipient) {
        const seed = await consensusManager.recipientRandomSeed(recipient);
        return who.signMessage(ethers.getBytes(seed));
    }

    // Submits a transaction through ConsensusMain and returns its id and the
    // ghost recipient it was deployed to.
    async function submitTransaction(numOfInitialValidators) {
        const receipt = await (
            await consensusMain.addTransaction(
                outsider.address,
                ethers.ZeroAddress,
                numOfInitialValidators,
                0,
                "0x01",
            )
        ).wait();
        const created = receipt.logs
            .map((log) => {
                try {
                    return consensusMain.interface.parseLog(log);
                } catch (_error) {
                    return null;
                }
            })
            .find((event) => event && event.name === "NewTransaction");
        expect(created, "ConsensusMain did not emit NewTransaction").to.not.equal(
            undefined,
        );
        return { txId: created.args.txId, recipient: created.args.recipient };
    }

    // Runs one full acceptance round: the designated activator activates, the
    // leader proposes, and every validator in the round commits then reveals an
    // Agree vote. This is the path that reaches Accepted, which is the
    // precondition both finalizeTransaction and submitAppeal check.
    async function runRoundToAccepted(id) {
        const transaction = await transactions.getTransaction(id);
        const activator = signerFor(transaction.activator);
        await consensusMain
            .connect(activator)
            .activateTransaction(id, await signSeed(activator, transaction.recipient));

        const active = await transactions.getTransaction(id);
        const round = active.roundData[active.roundData.length - 1];
        const leader = signerFor(round.roundValidators[round.leaderIndex]);
        await consensusMain
            .connect(leader)
            .proposeReceipt(
                id,
                "0x02",
                1,
                [],
                await signSeed(leader, transaction.recipient),
            );

        const proposed = await transactions.getTransaction(id);
        const validators = proposed.roundData[
            proposed.roundData.length - 1
        ].roundValidators;
        expect(validators.length).to.be.greaterThan(0);

        const commitments = new Map();
        for (let index = 0; index < validators.length; index++) {
            const nonce = BigInt(index + 1);
            const voteHash = ethers.keccak256(
                ethers.solidityPacked(
                    ["address", "uint8", "uint256"],
                    [validators[index], VoteType.Agree, nonce],
                ),
            );
            await consensusMain
                .connect(signerFor(validators[index]))
                .commitVote(id, voteHash);
            commitments.set(validators[index], { nonce, voteHash });
        }

        for (const [validator, { nonce, voteHash }] of commitments) {
            await consensusMain
                .connect(signerFor(validator))
                .revealVote(id, voteHash, VoteType.Agree, nonce);
        }
    }

    beforeEach(async function () {
        await deployments.fixture();
        signers = await ethers.getSigners();
        [outsider] = signers;

        transactions = await ethers.getContractAt(
            "Transactions",
            (await deployments.get("Transactions")).address,
        );
        consensusMain = await ethers.getContractAt(
            "ConsensusMain",
            (await deployments.get("ConsensusMain")).address,
        );
        consensusManager = await ethers.getContractAt(
            "ConsensusManager",
            (await deployments.get("ConsensusManager")).address,
        );
        Status = readTransactionStatusEnum();
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

    // The tests below drive the real consensus flow so the gate is shown to
    // admit ConsensusMain, not just to reject outsiders. Without them the suite
    // would still pass if the gate rejected every caller, including the one
    // legitimate caller in the tree.

    it("still activates a transaction through ConsensusMain", async function () {
        const { txId: id, recipient } = await submitTransaction(2);
        const before = await transactions.getTransaction(id);
        expect(before.status).to.equal(BigInt(Status.Pending));

        const activator = signerFor(before.activator);
        await consensusMain
            .connect(activator)
            .activateTransaction(id, await signSeed(activator, recipient));

        expect((await transactions.getTransaction(id)).status).to.equal(
            BigInt(Status.Proposing),
        );
    });

    it("still accepts a transaction through ConsensusMain", async function () {
        const { txId: id } = await submitTransaction(2);
        await runRoundToAccepted(id);

        expect((await transactions.getTransaction(id)).status).to.equal(
            BigInt(Status.Accepted),
        );
    });

    it("still finalizes a transaction through ConsensusMain", async function () {
        const { txId: id } = await submitTransaction(2);
        await runRoundToAccepted(id);

        await consensusMain.connect(outsider).finalizeTransaction(id);

        expect((await transactions.getTransaction(id)).status).to.equal(
            BigInt(Status.Finalized),
        );
    });

    it("still submits an appeal through ConsensusMain", async function () {
        // The appeal round is round 1 and asks for
        // FeeManager.VALIDATORS_PER_ROUND[1] == 7 validators, but the deploy
        // fixture only registers 5, so MockGenStaking would revert
        // NotEnoughValidators before the gated call is ever reached. Register
        // enough validators to get past that.
        const genStaking = await ethers.getContractAt(
            "MockGenStaking",
            (await deployments.get("MockGenStaking")).address,
        );
        await genStaking.addValidators(
            (await ethers.getSigners()).slice(6, 16).map((s) => s.address),
        );

        const { txId: id } = await submitTransaction(2);
        await runRoundToAccepted(id);

        await consensusMain.connect(outsider).submitAppeal(id, { value: 0 });

        expect((await transactions.getTransaction(id)).status).to.equal(
            BigInt(Status.AppealCommitting),
        );
    });
});
