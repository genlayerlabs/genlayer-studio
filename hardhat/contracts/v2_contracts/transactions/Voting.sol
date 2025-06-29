// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { ITransactions } from "./interfaces/ITransactions.sol";

contract Voting {
	function getMajorityVote(
		ITransactions.RoundData memory roundData
	) external pure returns (ITransactions.ResultType result) {
		result = ITransactions.ResultType.Idle;
		uint256 validatorCount = roundData.roundValidators.length;
		uint256[] memory voteCounts = new uint256[](
			uint256(type(ITransactions.VoteType).max) + 1
		);
		for (uint256 i = 0; i < roundData.validatorVotes.length; i++) {
			voteCounts[uint256(roundData.validatorVotes[i])]++;
		}

		uint256 maxVotes = 0;
		ITransactions.VoteType majorityVote = ITransactions.VoteType(0);
		for (uint256 i = 0; i < voteCounts.length; i++) {
			if (voteCounts[i] > maxVotes) {
				maxVotes = voteCounts[i];
				majorityVote = ITransactions.VoteType(i);
			}
		}
		if (maxVotes == validatorCount) {
			if (majorityVote == ITransactions.VoteType.Agree) {
				result = ITransactions.ResultType.MajorityAgree;
			} else {
				result = ITransactions.ResultType.MajorityDisagree;
			}
		} else if (maxVotes > validatorCount / 2) {
			if (majorityVote == ITransactions.VoteType.Agree) {
				result = ITransactions.ResultType.Agree;
			} else if (majorityVote == ITransactions.VoteType.Disagree) {
				result = ITransactions.ResultType.Disagree;
			} else if (majorityVote == ITransactions.VoteType.Timeout) {
				result = ITransactions.ResultType.Timeout;
			} else if (
				majorityVote == ITransactions.VoteType.DeterministicViolation
			) {
				result = ITransactions.ResultType.DeterministicViolation;
			}
		} else {
			result = ITransactions.ResultType.NoMajority;
		}
	}
}