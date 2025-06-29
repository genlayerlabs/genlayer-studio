// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { IGenStaking } from "../../interfaces/IGenStaking.sol";
import { IMessages } from "../../interfaces/IMessages.sol";
import { IIdleness } from "./IIdleness.sol";
import { Rounds } from "../Rounds.sol";
import { Voting } from "../Voting.sol";
import { Utils } from "../Utils.sol";

interface ITransactions {
	struct Transaction {
		bytes32 id;
		address sender;
		address recipient;
		uint256 numOfInitialValidators;
		uint256 txSlot;
		address activator;
		TransactionStatus status;
		TransactionStatus previousStatus;
		Timestamps timestamps;
		bytes32 randomSeed;
		bool onAcceptanceMessages;
		ResultType result;
		ReadStateBlockRange readStateBlockRange;
		bytes txData;
		bytes txReceipt;
		IMessages.SubmittedMessage[] messages;
		address[] consumedValidators;
		RoundData[] roundData;
	}

	struct IdleTransactionInfo {
		bytes32 id;
		TransactionStatus status;
		uint256 currentSlot;
		address[] idleValidators;
		address[] newValidators;
	}

	struct UpdateTransactionInfo {
		bytes32 id;
		address activator;
		Timestamps timestamps;
		address[] consumedValidators;
		uint256 round;
		RoundData roundData;
	}

	struct Timestamps {
		uint256 created;
		uint256 pending;
		uint256 activated;
		uint256 proposed;
		uint256 committed;
		uint256 lastVote;
	}

	struct RoundData {
		uint256 round;
		uint256 leaderIndex;
		uint256 votesCommitted;
		uint256 votesRevealed;
		uint256 appealBond;
		uint256 rotationsLeft;
		ResultType result;
		address[] roundValidators;
		bytes32[] validatorVotesHash;
		VoteType[] validatorVotes;
	}

	struct NewRoundData {
		uint256 round;
		uint256 leaderIndex;
		address[] roundValidators;
	}

	struct ActivationInfo {
		address sender;
		address recepientAddress;
		uint256 numOfInitialValidators;
		bool initialActivation;
		uint256 rotationsLeft;
	}

	struct ExternalContracts {
		address genConsensus;
		IGenStaking staking;
		Rounds rounds;
		Voting voting;
		IIdleness idleness;
		Utils utils;
	}

	enum TransactionStatus {
		Uninitialized, // 0
		Pending, // 1
		Proposing, // 2
		Committing, // 3
		Revealing, // 4
		Accepted, // 5
		Undetermined, // 6
		Finalized, // 7
		Canceled, // 8
		AppealRevealing, // 9
		AppealCommitting, // 10
		ReadyToFinalize // 11
	}
	enum VoteType {
		NotVoted,
		Agree,
		Disagree,
		Timeout,
		DeterministicViolation
	}

	enum ResultType {
		Idle, // 0
		Agree, // 1
		Disagree, // 2
		Timeout, // 3
		DeterministicViolation, // 4
		NoMajority, // 5
		MajorityAgree, // 6
		MajorityDisagree // 7
	}

	struct ReadStateBlockRange {
		uint256 activationBlock;
		uint256 processingBlock;
		uint256 proposalBlock;
	}

	function addNewTransaction(
		bytes32 _txId,
		Transaction memory newTx
	) external returns (bytes32);

	function activateTransaction(
		bytes32 _txId,
		address _activator,
		bytes32 _randomSeed
	)
		external
		returns (
			address recepient,
			uint256 leaderIndex,
			address[] memory validators
		);

	function proposeTransactionReceipt(
		bytes32 _txId,
		address _leader,
		uint256 _processingBlock,
		bytes calldata _txReceipt,
		IMessages.SubmittedMessage[] calldata _messages
	)
		external
		returns (
			address recipient,
			bool leaderTimeout,
			address newLeader,
			uint256 round
		);

	function commitVote(
		bytes32 _txId,
		bytes32 _commitHash,
		address _validator
	) external returns (bool isLastVote);

	function revealVote(
		bytes32 _txId,
		bytes32 _voteHash,
		VoteType _voteType,
		address _validator
	)
		external
		returns (
			bool isLastVote,
			ResultType result,
			address recipient,
			uint256 round,
			bool hasMessagesOnAcceptance,
			uint256 rotationsLeft,
			NewRoundData memory newRoundData
		);

	function finalizeTransaction(
		bytes32 _txId
	) external returns (address recipient, uint256 lastVoteTimestamp);

	function cancelTransaction(
		bytes32 _txId,
		address _sender
	) external returns (address recipient);

	function submitAppeal(
		bytes32 _txId,
		uint256 _appealBond
	) external returns (address[] memory appealValidators, uint256 round);

	function rotateLeader(bytes32 _txId) external returns (address);

	function getTransactionRecipient(
		bytes32 _txId
	) external view returns (address recipient);

	function getTransaction(
		bytes32 _txId
	) external view returns (Transaction memory);

	function hasMessagesOnFinalization(
		bytes32 _txId
	) external view returns (bool itHasMessagesOnFinalization);

	function getMessagesForTransaction(
		bytes32 _txId
	) external view returns (IMessages.SubmittedMessage[] memory);

	function setExternalContracts(
		address _genConsensus,
		address _staking,
		address _rounds,
		address _voting,
		address _idleness,
		address _utils
	) external;
}