// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { Initializable } from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import { Ownable2StepUpgradeable } from "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import { ReentrancyGuardUpgradeable } from "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import { AccessControlUpgradeable } from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";

import { Rounds } from "./transactions/Rounds.sol";
import { Voting } from "./transactions/Voting.sol";
import { Idleness } from "./transactions/Idleness.sol";
import { Utils } from "./transactions/Utils.sol";
import { ITransactions } from "./transactions/interfaces/ITransactions.sol";
import { IMessages } from "./interfaces/IMessages.sol";
import { Errors } from "./utils/Errors.sol";
import { IGenStaking } from "./interfaces/IGenStaking.sol";
import { ArrayUtils } from "./utils/ArrayUtils.sol";

contract Transactions is
	Initializable,
	Ownable2StepUpgradeable,
	ReentrancyGuardUpgradeable,
	AccessControlUpgradeable,
	ITransactions
{
	/// @notice Mapping of transaction IDs to transactions
	mapping(bytes32 => Transaction) public transactions;

	/// @notice External contracts
	ExternalContracts public contracts;

	/// @notice Gap for future upgrades
	uint256[50] private __gap;

	/// @notice Initializes the contract
	/// @param _genConsensus The address of the genConsensus contract
	function initialize(address _genConsensus) public initializer {
		__Ownable2Step_init();
		__Ownable_init(msg.sender);
		__ReentrancyGuard_init();
		__AccessControl_init();

		contracts.genConsensus = _genConsensus;
	}

	/// @notice Modifier to check if the caller is the genConsensus contract
	modifier onlyGenConsensus() {
		if (msg.sender != contracts.genConsensus) {
			revert Errors.CallerNotConsensus();
		}
		_;
	}

	receive() external payable {}

	/// @notice Adds a new transaction to the transactions mapping
	/// @param txId The ID of the transaction
	/// @param newTx The new transaction
	/// @return txId The ID of the transaction
	function addNewTransaction(
		bytes32 txId,
		Transaction memory newTx
	) external onlyGenConsensus returns (bytes32) {
		transactions[txId] = newTx;
		return txId;
	}

	/// @notice Activates a transaction
	/// @param _txId The ID of the transaction
	/// @param _activator The activator of the transaction
	/// @param _randomSeed The random seed of the transaction
	/// @return recipient The recipient of the transaction
	/// @return leaderIndex The leader index of the transaction
	/// @return validators The validators of the transaction
	function activateTransaction(
		bytes32 _txId,
		address _activator,
		bytes32 _randomSeed
	)
		external
		returns (
			address recipient,
			uint256 leaderIndex,
			address[] memory validators
		)
	{
		Transaction storage transaction = transactions[_txId];

		// Check if the status is correct
		contracts.utils.checkStatus(
			transaction.status,
			TransactionStatus.Pending
		);

		// Check if the activator is idle, and replace it if so
		_updateTransaction(contracts.idleness.checkIdle(transaction));

		// Check if the caller is the correct activator
		uint256 round = _getRound(transaction);
		if (round == 0 && transaction.activator != _activator) {
			revert Errors.CallerNotActivator();
		}

		// Set the status to proposing
		transaction.status = TransactionStatus.Proposing;

		// Create a new round and update the transaction
		UpdateTransactionInfo memory updateInfo;
		(validators, leaderIndex, updateInfo) = contracts.rounds.createNewRound(
			transaction,
			round,
			0,
			_randomSeed,
			2,
			false
		);
		_updateTransaction(updateInfo);

		// Set the timestamp and random seed
		transaction.timestamps.activated = block.timestamp;
		transaction.randomSeed = _randomSeed;

		// Return the recipient
		recipient = transaction.recipient;
	}

	/// @notice Proposes a transaction receipt
	/// @param _txId The ID of the transaction
	/// @param _leader The leader of the transaction
	/// @param _txReceipt The transaction receipt
	/// @param _messages The messages of the transaction
	/// @return recipient The recipient of the transaction
	/// @return leaderFailedToPropose Whether the leader failed to propose
	function proposeTransactionReceipt(
		bytes32 _txId,
		address _leader,
		uint256 /*_processingBlock*/,
		bytes calldata _txReceipt,
		IMessages.SubmittedMessage[] calldata _messages
	)
		external
		onlyGenConsensus
		returns (
			address recipient,
			bool leaderFailedToPropose,
			address newLeader,
			uint256 round
		)
	{
		Transaction storage transaction = transactions[_txId];

		// Check if the status is correct
		contracts.utils.checkStatus(
			transaction.status,
			TransactionStatus.Proposing
		);

		// Check if the activator is idle, and replace it if so
		_updateTransaction(contracts.idleness.checkIdle(transaction));

		// Check if the caller is the leader
		round = _getRound(transaction);
		if (
			_leader !=
			transaction.roundData[round].roundValidators[
				transaction.roundData[round].leaderIndex
			]
		) {
			revert Errors.CallerNotLeader();
		}

		// Check if the receipt is empty or not
		if (_txReceipt.length == 0) {
			// No receipt, so we need to rotate the leader, if there are rotations left
			uint256 rotationsLeft = transaction.roundData[round].rotationsLeft;
			if (rotationsLeft == 0) {
				// No rotations left, so we need to undetermined the transaction
				transaction.status = TransactionStatus.Undetermined;
			} else {
				// Rotate the leader
				newLeader = _rotateLeader(transaction);
			}

			// Flag that the leader failed to propose
			leaderFailedToPropose = true;
		} else {
			_commitVote(transaction, _txReceipt, _messages);
			_checkMessagesOnAcceptance(transaction, _messages);
		}

		// Set the timestamp
		transaction.timestamps.proposed = block.timestamp;

		// Return the recipient
		recipient = transaction.recipient;
	}

	/// @notice Commits a vote
	/// @param _txId The ID of the transaction
	/// @param _commitHash The commit hash of the vote
	/// @param _validator The validator that committed the vote
	/// @return isLastVote Whether the vote is the last vote
	function commitVote(
		bytes32 _txId,
		bytes32 _commitHash,
		address _validator
	) external onlyGenConsensus returns (bool isLastVote) {
		Transaction storage transaction = transactions[_txId];
		uint256 round = transaction.roundData.length - 1;
		RoundData storage roundData = transaction.roundData[round];

		// Check if the status is correct
		contracts.utils.checkStatus(
			transaction.status,
			round % 2 == 0
				? TransactionStatus.Committing
				: TransactionStatus.AppealCommitting
		);

		// Check if the activator is idle, and replace it if so
		_updateTransaction(contracts.idleness.checkIdle(transaction));

		// Process votes
		uint256 votesCommitted = ++roundData.votesCommitted;
		if (votesCommitted == roundData.roundValidators.length) {
			isLastVote = true;
			transaction.status = round % 2 == 0
				? TransactionStatus.Revealing
				: TransactionStatus.AppealRevealing;
			transaction.timestamps.committed = block.timestamp;
			transaction.timestamps.lastVote = block.timestamp;
		} else if (votesCommitted > roundData.roundValidators.length) {
			revert Errors.VoteAlreadyCommitted();
		}

		// Get validator index in current round validators array
		(uint256 validatorIndex, bool isFirstValidator) = ArrayUtils.getIndex(
			roundData.roundValidators,
			_validator
		);
		if (validatorIndex == 0 && !isFirstValidator) {
			revert Errors.ValidValidatorNotFound();
		}
		if (roundData.validatorVotesHash[validatorIndex] != bytes32(0)) {
			revert Errors.VoteAlreadyCommitted();
		}

		// Register vote
		roundData.validatorVotesHash[validatorIndex] = _commitHash;
	}

	/// @notice Reveals a vote
	/// @param _txId The ID of the transaction
	/// @param _voteHash The vote hash
	/// @param _voteType The vote type
	/// @param _validator The validator that revealed the vote
	/// @return isLastVote Whether the vote is the last vote
	/// @return majorVoted The majority vote
	function revealVote(
		bytes32 _txId,
		bytes32 _voteHash,
		VoteType _voteType,
		address _validator
	)
		external
		onlyGenConsensus
		returns (
			bool isLastVote,
			ResultType majorVoted,
			address recipient,
			uint256 round,
			bool hasMessagesOnAcceptance,
			uint256 rotationsLeft,
			NewRoundData memory newRoundData
		)
	{
		Transaction storage transaction = transactions[_txId];
		round = transaction.roundData.length - 1;
		RoundData storage roundData = transaction.roundData[round];

		// Check if the status is correct
		contracts.utils.checkStatus(
			transaction.status,
			round % 2 == 0
				? TransactionStatus.Revealing
				: TransactionStatus.AppealRevealing
		);

		// Check if the activator is idle, and slash it if so (no replacement)
		contracts.idleness.checkIdle(transaction);

		// Process revealing votes
		uint256 votesRevealed = ++roundData.votesRevealed;
		if (votesRevealed == roundData.roundValidators.length) {
			isLastVote = true;
		} else if (votesRevealed > roundData.roundValidators.length) {
			revert Errors.VoteAlreadyRevealed();
		}

		// Get validator index in current round validators array
		(uint256 validatorIndex, bool isFirstValidator) = ArrayUtils.getIndex(
			roundData.roundValidators,
			_validator
		);
		if (validatorIndex == 0 && !isFirstValidator) {
			revert Errors.ValidValidatorNotFound();
		}
		if (roundData.validatorVotesHash[validatorIndex] != _voteHash) {
			revert Errors.InvalidVote();
		}

		// Register vote
		roundData.validatorVotes[validatorIndex] = _voteType;

		// Get the majority vote
		(majorVoted, newRoundData, hasMessagesOnAcceptance) = _getMajorityVote(
			transaction,
			roundData,
			round,
			isLastVote
		);

		recipient = transaction.recipient;
		rotationsLeft = roundData.rotationsLeft;
	}

	/// @notice Finalizes a transaction
	/// @param _txId The ID of the transaction
	/// @return recipient The recipient of the transaction
	/// @return lastVoteTimestamp The timestamp of the last vote
	function finalizeTransaction(
		bytes32 _txId
	) external returns (address recipient, uint256 lastVoteTimestamp) {
		Transaction storage transaction = transactions[_txId];
		ITransactions.TransactionStatus status = transaction.status;

		if (
			status != ITransactions.TransactionStatus.Accepted &&
			status != ITransactions.TransactionStatus.Undetermined
		) {
			revert Errors.TransactionNotAcceptedOrUndetermined();
		}

		// Set the status to finalized
		transaction.status = TransactionStatus.Finalized;

		// Return the recipient
		recipient = transaction.recipient;

		// Return the last vote timestamp
		lastVoteTimestamp = transaction.timestamps.lastVote;
	}

	/// @notice Cancels a transaction
	/// @param _txId The ID of the transaction
	/// @param _sender The sender of the transaction
	/// @return recipient The recipient of the transaction
	function cancelTransaction(
		bytes32 _txId,
		address _sender
	) external onlyGenConsensus returns (address recipient) {
		Transaction storage transaction = transactions[_txId];

		// Check if the status is correct
		contracts.utils.checkStatus(
			transaction.status,
			TransactionStatus.Pending
		);

		// Check if the sender is the correct sender
		if (transaction.sender != _sender) {
			revert Errors.CallerNotSender();
		}

		// Set the status to canceled
		transaction.status = TransactionStatus.Canceled;

		// Return the recipient
		recipient = transaction.recipient;
	}

	/// @notice Submits an appeal
	/// @param _txId The ID of the transaction
	/// @param _appealBond The appeal bond
	/// @return appealValidators The validators that appealed
	/// @return round The round of the appeal
	function submitAppeal(
		bytes32 _txId,
		uint256 _appealBond
	) external returns (address[] memory appealValidators, uint256 round) {
		Transaction storage transaction = transactions[_txId];

		// Check if the status is correct
		if (
			transaction.status != TransactionStatus.Accepted &&
			transaction.status != TransactionStatus.Undetermined
		) {
			revert Errors.InvalidTransactionStatus();
		}

		// Create a new round
		round = transaction.roundData.length - 1;
		if (round % 2 == 1) {
			RoundData memory roundData = contracts.rounds.createAnEmptyRound();
			transactions[_txId].roundData.push(roundData);
			round++;
		}

		if (_appealBond < _calculateMinAppealBond(_txId, round + 1)) {
			revert Errors.AppealBondTooLow();
		}

		// Create a new round
		UpdateTransactionInfo memory updateInfo;
		(appealValidators, , updateInfo) = contracts.rounds.createNewRound(
			transaction,
			round + 1,
			_appealBond,
			transaction.randomSeed,
			2,
			false
		);
		_updateTransaction(updateInfo);
		++round;

		// Set the previous status
		transaction.previousStatus = transaction.status;

		// Set the status to appeal committing
		transaction.status = TransactionStatus.AppealCommitting;
	}

	/// @notice Rotates the leader of a transaction
	/// @param _txId The ID of the transaction
	/// @return newLeader The new leader of the transaction
	function rotateLeader(
		bytes32 _txId
	) external onlyGenConsensus returns (address) {
		return _rotateLeader(transactions[_txId]);
	}

	/// @notice Gets the recipient of a transaction
	/// @param txId The ID of the transaction
	/// @return recipient The recipient of the transaction
	function getTransactionRecipient(
		bytes32 txId
	) external view returns (address recipient) {
		recipient = transactions[txId].recipient;
	}

	/// @notice Gets a transaction
	/// @param txId The ID of the transaction
	/// @return transaction The transaction
	function getTransaction(
		bytes32 txId
	) external view returns (Transaction memory) {
		return transactions[txId];
	}

	/// @notice Checks if a transaction has messages on finalization
	/// @param _txId The ID of the transaction
	/// @return hasMessagesOnFinalization Whether the transaction has messages on finalization
	function hasMessagesOnFinalization(
		bytes32 _txId
	) external view returns (bool) {
		return transactions[_txId].messages.length > 0;
	}

	/// @notice Gets the messages for a transaction
	/// @param _txId The ID of the transaction
	/// @return messages The messages for the transaction
	function getMessagesForTransaction(
		bytes32 _txId
	) external view returns (IMessages.SubmittedMessage[] memory) {
		return transactions[_txId].messages;
	}

	// Internal functions

	function _getRound(
		Transaction storage _tx
	) internal view returns (uint256) {
		return _tx.roundData.length > 0 ? _tx.roundData.length - 1 : 0;
	}

	function _updateTransaction(UpdateTransactionInfo memory _info) internal {
		bytes32 txId = _info.id;
		Transaction storage transaction = transactions[txId];

		if (_info.activator != address(0)) {
			transaction.activator = _info.activator;
		}

		if (_info.consumedValidators.length > 0) {
			transaction.consumedValidators = _info.consumedValidators;
		}

		if (_info.round == transaction.roundData.length) {
			// new round
			transaction.roundData.push(_info.roundData);
		} else {
			// update round
			transaction.roundData[_info.round] = _info.roundData;
		}

		if (_info.timestamps.pending != 0) {
			transaction.timestamps.pending = _info.timestamps.pending;
		}
		if (_info.timestamps.activated != 0) {
			transaction.timestamps.activated = _info.timestamps.activated;
		}
		if (_info.timestamps.proposed != 0) {
			transaction.timestamps.proposed = _info.timestamps.proposed;
		}
		if (_info.timestamps.committed != 0) {
			transaction.timestamps.committed = _info.timestamps.committed;
		}
	}

	function _rotateLeader(
		Transaction storage _tx
	) internal returns (address newLeader) {
		uint256 round = _getRound(_tx);
		ITransactions.UpdateTransactionInfo memory info;
		(newLeader, info) = contracts.rounds.rotateLeader(_tx);
		_updateTransaction(info);
		--_tx.roundData[round].rotationsLeft;
		_tx.status = TransactionStatus.Proposing;
		_tx.timestamps.activated = block.timestamp;
	}

	function _commitVote(
		Transaction storage _tx,
		bytes calldata _txReceipt,
		IMessages.SubmittedMessage[] calldata _messages
	) internal {
		_tx.status = TransactionStatus.Committing;
		_tx.txReceipt = _txReceipt;
		_tx.messages = _messages;
	}

	function _checkMessagesOnAcceptance(
		Transaction storage _tx,
		IMessages.SubmittedMessage[] calldata _messages
	) internal {
		for (uint256 i = 0; i < _messages.length; i++) {
			if (_messages[i].onAcceptance) {
				_tx.onAcceptanceMessages = true;
				break;
			}
		}
	}

	function _getMajorityVote(
		Transaction storage _tx,
		RoundData memory roundData,
		uint256 round,
		bool isLastVote
	)
		internal
		returns (
			ResultType majorVoted,
			NewRoundData memory newRoundData,
			bool hasMessagesOnAcceptance
		)
	{
		majorVoted = ResultType(0);
		if (isLastVote) {
			majorVoted = contracts.voting.getMajorityVote(roundData);
			_tx.roundData[round].result = majorVoted;
			if (
				majorVoted == ResultType.MajorityAgree ||
				majorVoted == ResultType.Agree
			) {
				if (round % 2 == 0) {
					_tx.status = TransactionStatus.Accepted;
					hasMessagesOnAcceptance = _tx.onAcceptanceMessages;
				} else {
					_tx.status = _tx.previousStatus;
					_tx.previousStatus = TransactionStatus.AppealRevealing;
				}
			} else {
				if (round % 2 == 0) {
					_tx.status = TransactionStatus.Undetermined;
				} else {
					// Successfully appealed
					ITransactions.UpdateTransactionInfo memory updateInfo;
					_tx.status = TransactionStatus.Proposing;
					(
						newRoundData.roundValidators,
						newRoundData.leaderIndex,
						updateInfo
					) = contracts.rounds.createNewRound(
						_tx,
						round + 1,
						0,
						_tx.randomSeed,
						2,
						false
					);
					_updateTransaction(updateInfo);
					newRoundData.round = round + 1;
				}
			}
			_tx.timestamps.lastVote = block.timestamp;
		}
	}

	function _calculateMinAppealBond(
		bytes32 /*_txId*/,
		uint256 /*_round*/
	) internal pure returns (uint256 minAppealBond) {
		// TODO: Implement the logic to calculate the minimum appeal bond
		minAppealBond = 0;
	}

	function _checkActivator(bytes32 _txId, address _activator) internal view {
		if (transactions[_txId].activator != _activator) {
			revert Errors.CallerNotActivator();
		}
		// TODO: Check if it is the next activator
	}

	function _checkStatusCommittedOrRevealed(
		uint256 round,
		TransactionStatus status,
		bool checkCommitted
	) internal pure {
		bool isRegularRound = round % 2 == 0;
		if (checkCommitted) {
			if (isRegularRound) {
				// regular round
				if (status != TransactionStatus.Committing) {
					revert Errors.TransactionNotCommitting();
				}
			} else {
				// appeal round
				if (status != TransactionStatus.AppealCommitting) {
					revert Errors.TransactionNotAppealCommitting();
				}
			}
		} else {
			if (isRegularRound) {
				// regular round
				if (status != TransactionStatus.Revealing) {
					revert Errors.TransactionNotRevealing();
				}
			} else {
				// appeal round
				if (status != TransactionStatus.AppealRevealing) {
					revert Errors.TransactionNotAppealRevealing();
				}
			}
		}
	}

	// Setters

	function setExternalContracts(
		address _genConsensus,
		address _staking,
		address _rounds,
		address _voting,
		address _idleness,
		address _utils
	) external onlyOwner {
		contracts = ExternalContracts({
			genConsensus: _genConsensus,
			staking: IGenStaking(_staking),
			rounds: Rounds(_rounds),
			voting: Voting(_voting),
			idleness: Idleness(_idleness),
			utils: Utils(_utils)
		});
	}
}