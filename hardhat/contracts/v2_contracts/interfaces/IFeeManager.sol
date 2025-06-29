// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { ITransactions } from "../transactions/interfaces/ITransactions.sol";

interface IFeeManager {
	enum RoundTypes {
		EMPTY_ROUND,
		NORMAL_REWARDS_ROUND,
		SKIP_REWARDS_ROUND,
		SPLIT_PREV_APPEAL_BOND_ROUND,
		LEADER_TIMEOUT_50_PERCENT,
		LEADER_TIMEOUT_50_PERCENT_PREV_APPEAL_BOND,
		LEADER_TIMEOUT_150_PERCENT_PREV_NORMAL_ROUND,
		APPEAL_VALIDATOR_SUCCESS,
		APPEAL_VALIDATOR_UNSUCCESSFUL,
		APPEAL_LEADER_SUCCESS,
		APPEAL_LEADER_UNSUCCESSFUL,
		APPEAL_LEADER_TIMEOUT_SUCCESS,
		APPEAL_LEADER_TIMEOUT_UNSUCCESSFUL
	}

	struct FeesDistribution {
		uint leaderTimeout;
		uint validatorsTimeout;
		uint appealRounds;
		uint rollupStorageFee;
		uint rollupGenVMFee;
		uint totalMessageFees;
		uint[] rotations;
	}

	struct RotationRoundFees {
		RoundTypes roundType;
		ITransactions.ResultType result;
		address leader; // or appealer
		uint256 appealBond;
		address[] validators;
		ITransactions.VoteType[] validatorsVotes;
	}

	struct AppealerRewards {
		uint256 totalRewards;
		address appealerAddress;
	}

	function getValidatorsPerRound(
		uint256 round
	) external view returns (uint256);

	function currentRoundForTx(
		bytes32 txId
	) external view returns (uint currentRound);

	// Calculate fees for a new round
	function calculateRoundFees(
		bytes32 _txId,
		FeesDistribution memory _feesDistribution,
		uint256 _numOfValidators,
		uint256 round
	) external view returns (uint256 totalFeesToPay);

	function topUpFees(
		bytes32 _txId,
		FeesDistribution memory _feesDistribution,
		uint256 _amount,
		bool _performFeeChecks,
		address _sender
	) external;

	function topUpAndSubmitAppeal(
		bytes32 _txId,
		FeesDistribution memory _feesDistribution,
		uint256 _amount,
		address _appealer,
		bool _performFeeChecks
	) external returns (bool feesForNewRoundApproved);

	function addAppealRound(
		bytes32 txId,
		uint256 round,
		uint256 appealBond,
		address _appealer
	) external returns (bool feesForNewRoundApproved);

	// Commit final fees for transaction
	function distributeFees(bytes32 _txId) external;

	function recordProposedReceipt(
		bytes32 _txId,
		uint256 _round,
		address _leader,
		bool _leaderTimeout
	) external;

	function recordRevealedVote(
		bytes32 _txId,
		uint256 _round,
		address _validator,
		bool _isLastVote,
		ITransactions.VoteType _voteType,
		ITransactions.ResultType _result
	) external returns (bool feesForNewRoundApproved);

	event FeesDeposited(
		bytes32 indexed txId,
		address indexed depositor,
		uint256 amount
	);
	event FeesCommitted(bytes32 indexed txId, uint256 amount);
	event InsufficientFees(bytes32 indexed txId, address indexed account);
	event FeesDistributed(bytes32 indexed txId, uint256 amount);
	event FeesIssuedBack(
		bytes32 indexed txId,
		address indexed account,
		uint256 amount
	);
}