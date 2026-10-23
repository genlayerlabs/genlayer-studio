const { expect } = require("chai");
const { ethers } = require("hardhat");

const VoteType = {
    NotVoted: 0,
    Agree: 1,
    Disagree: 2,
    Timeout: 3,
    DeterministicViolation: 4,
};

const ResultType = {
    Idle: 0,
    Agree: 1,
    Disagree: 2,
    Timeout: 3,
    DeterministicViolation: 4,
    NoMajority: 5,
    MajorityAgree: 6,
    MajorityDisagree: 7,
};

const resultNames = Object.fromEntries(
    Object.entries(ResultType).map(([name, value]) => [value, name])
);

function roundData(votes) {
    return {
        round: 1,
        leaderIndex: 0,
        votesCommitted: votes.length,
        votesRevealed: votes.length,
        appealBond: 0,
        rotationsLeft: 0,
        result: ResultType.Idle,
        roundValidators: votes.map(() => ethers.ZeroAddress),
        validatorVotesHash: votes.map(() => ethers.ZeroHash),
        validatorVotes: votes,
    };
}

describe("Voting.getMajorityVote", function () {
    let voting;

    before(async function () {
        const factory = await ethers.getContractFactory("Voting");
        voting = await factory.deploy();
        await voting.waitForDeployment();
    });

    it("returns Timeout for a unanimous timeout vote", async function () {
        const votes = Array(5).fill(VoteType.Timeout);
        const result = await voting.getMajorityVote(roundData(votes));
        expect(resultNames[Number(result)]).to.equal("Timeout");
    });

    it("returns DeterministicViolation for a unanimous violation vote", async function () {
        const votes = Array(5).fill(VoteType.DeterministicViolation);
        const result = await voting.getMajorityVote(roundData(votes));
        expect(resultNames[Number(result)]).to.equal("DeterministicViolation");
    });

    it("still returns MajorityAgree for a unanimous agree vote", async function () {
        const votes = Array(5).fill(VoteType.Agree);
        const result = await voting.getMajorityVote(roundData(votes));
        expect(resultNames[Number(result)]).to.equal("MajorityAgree");
    });

    it("still returns MajorityDisagree for a unanimous disagree vote", async function () {
        const votes = Array(5).fill(VoteType.Disagree);
        const result = await voting.getMajorityVote(roundData(votes));
        expect(resultNames[Number(result)]).to.equal("MajorityDisagree");
    });

    it("still returns MajorityDisagree for a unanimous not-voted vote", async function () {
        const votes = Array(5).fill(VoteType.NotVoted);
        const result = await voting.getMajorityVote(roundData(votes));
        expect(resultNames[Number(result)]).to.equal("MajorityDisagree");
    });

    it("returns Timeout for a non-unanimous majority timeout vote", async function () {
        const votes = [
            VoteType.Timeout,
            VoteType.Timeout,
            VoteType.Timeout,
            VoteType.Agree,
            VoteType.Disagree,
        ];
        const result = await voting.getMajorityVote(roundData(votes));
        expect(resultNames[Number(result)]).to.equal("Timeout");
    });

    it("returns DeterministicViolation for a non-unanimous majority violation vote", async function () {
        const votes = [
            VoteType.DeterministicViolation,
            VoteType.DeterministicViolation,
            VoteType.DeterministicViolation,
            VoteType.Agree,
            VoteType.Disagree,
        ];
        const result = await voting.getMajorityVote(roundData(votes));
        expect(resultNames[Number(result)]).to.equal("DeterministicViolation");
    });

    it("returns NoMajority when no vote type reaches a majority", async function () {
        const votes = [
            VoteType.Agree,
            VoteType.Agree,
            VoteType.Disagree,
            VoteType.Disagree,
            VoteType.Timeout,
        ];
        const result = await voting.getMajorityVote(roundData(votes));
        expect(resultNames[Number(result)]).to.equal("NoMajority");
    });
});
