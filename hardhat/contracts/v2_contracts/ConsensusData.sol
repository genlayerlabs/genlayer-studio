// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import "./transactions/interfaces/ITransactions.sol";
import "./interfaces/IQueues.sol";
import "./interfaces/IGenManager.sol";
import "./interfaces/IConsensusMain.sol";
import "./interfaces/IMessages.sol";

contract ConsensusData is
	Initializable,
	Ownable2StepUpgradeable,
	ReentrancyGuardUpgradeable,
	AccessControlUpgradeable
{
	ITransactions public transactions;
	IQueues public queues;
	IConsensusMain public consensusMain;
	struct TransactionData {
		// Basic transaction info
		address sender;
		address recipient;
		uint256 numOfInitialValidators;
		uint256 txSlot;
		uint256 timestamp;
		uint256 lastVoteTimestamp;
		bytes32 randomSeed;
		ITransactions.ResultType result;
		bytes txData;
		bytes txReceipt;
		IMessages.SubmittedMessage[] messages;
		// Validator info
		address[] validators;
		bytes32[] validatorVotesHash;
		ITransactions.VoteType[] validatorVotes;
		// Queue info
		IQueues.QueueType queueType;
		uint256 queuePosition;
		// Status info
		address activator;
		address leader;
		ITransactions.TransactionStatus status;
		uint256 committedVotesCount;
		uint256 revealedVotesCount;
		uint256 rotationsLeft;
	}

	/**
	 * @notice Allows the contract to receive Ether.
	 */
	receive() external payable {}

	/**
	 * @notice Initializes the contract with references to consensusMain, transactions, and queues.
	 * @param _consensusMain The address for the consensusMain contract.
	 * @param _transactions The address for the transactions contract.
	 * @param _queues The address for the queues contract.
	 */
	function initialize(
		address _consensusMain,
		address _transactions,
		address _queues
	) public initializer {
		__Ownable2Step_init();
		__Ownable_init(msg.sender);
		__ReentrancyGuard_init();
		__AccessControl_init();

		transactions = ITransactions(_transactions);
		queues = IQueues(_queues);
		consensusMain = IConsensusMain(_consensusMain);
	}

	/**
	 * @notice Retrieves full transaction data including round data and validator details.
	 * @param _tx_id The transaction identifier.
	 * @return txData TransactionData The full enriched transaction data.
	 */
	function getTransactionData(
		bytes32 _tx_id
	) external view returns (TransactionData memory) {
		ITransactions.Transaction memory transaction = transactions
			.getTransaction(_tx_id);
		bytes32 randomSeed = consensusMain
			.contracts()
			.genManager
			.recipientRandomSeed(transaction.recipient);
		// uint256 txSlot = queues.getTransactionQueuePosition(_tx_id);
		// address activator = consensusMain.getActivatorForTx(randomSeed, txSlot);
		address activator = transaction.activator;
		uint256 txSlot = transaction.txSlot;
		uint lastRound = transaction.roundData.length > 0
			? transaction.roundData.length - 1
			: 0;
		ITransactions.RoundData memory lastRoundData = transaction
			.roundData
			.length > 0
			? transaction.roundData[lastRound]
			: ITransactions.RoundData(
				0,
				0,
				0,
				0,
				0,
				0,
				ITransactions.ResultType(0),
				new address[](0),
				new bytes32[](0),
				new ITransactions.VoteType[](0)
			);
		uint validatorsCount = lastRoundData.roundValidators.length;
		uint leaderIndex = lastRoundData.leaderIndex;
		address[] memory validators = lastRoundData.roundValidators;
		address leader = validatorsCount > 0
			? validators[leaderIndex]
			: address(0);
		randomSeed = transaction.randomSeed != bytes32(0)
			? transaction.randomSeed
			: randomSeed;
		TransactionData memory txData = TransactionData({
			// Basic transaction info
			sender: transaction.sender,
			recipient: transaction.recipient,
			numOfInitialValidators: transaction.numOfInitialValidators,
			txSlot: txSlot,
			timestamp: transaction.timestamps.created,
			lastVoteTimestamp: transaction.timestamps.lastVote,
			randomSeed: randomSeed,
			result: lastRoundData.result,
			txData: transaction.txData,
			txReceipt: transaction.txReceipt,
			messages: transaction.messages,
			// Validator info
			validators: validators,
			validatorVotesHash: lastRoundData.validatorVotesHash,
			validatorVotes: lastRoundData.validatorVotes,
			// Queue info
			queueType: queues.getTransactionQueueType(_tx_id),
			queuePosition: queues.getTransactionQueuePosition(_tx_id),
			// Status info
			activator: activator,
			leader: leader,
			status: transaction.status,
			committedVotesCount: lastRoundData.votesCommitted,
			revealedVotesCount: lastRoundData.votesRevealed,
			rotationsLeft: lastRoundData.rotationsLeft
		});

		return txData;
	}

	function getValidatorsForLastAppeal(
		bytes32 _tx_id
	) external view returns (address[] memory) {
		ITransactions.Transaction memory transaction = transactions
			.getTransaction(_tx_id);
		uint256 lastRound = transaction.roundData.length > 0
			? transaction.roundData.length - 1
			: 0;
		if (lastRound > 0) {
			if (lastRound % 2 == 1) {
				return transaction.roundData[lastRound].roundValidators;
			} else {
				return transaction.roundData[lastRound - 1].roundValidators;
			}
		} else {
			return new address[](0);
		}
	}

	/**
	 * @notice Gets the validators used for the last round.
	 * @param _tx_id The transaction identifier.
	 * @return address[] List of validator addresses for the last round.
	 */
	function getValidatorsForLastRound(
		bytes32 _tx_id
	) external view returns (address[] memory) {
		ITransactions.Transaction memory transaction = transactions
			.getTransaction(_tx_id);
		uint256 lastRound = transaction.roundData.length > 0
			? transaction.roundData.length - 1
			: 0;
		return transaction.roundData[lastRound].roundValidators;
	}

	/**
	 * @notice Retrieves the result of the last appeal round if it exists.
	 * @param _tx_id The transaction identifier.
	 * @return ITransactions.ResultType The result type of the last appeal.
	 */
	function getLastAppealResult(
		bytes32 _tx_id
	) external view returns (ITransactions.ResultType) {
		ITransactions.Transaction memory transaction = transactions
			.getTransaction(_tx_id);
		uint256 lastRound = transaction.roundData.length > 0
			? transaction.roundData.length - 1
			: 0;
		if (lastRound > 0) {
			if (lastRound % 2 == 1) {
				return transaction.roundData[lastRound].result;
			} else {
				return transaction.roundData[lastRound - 1].result;
			}
		} else {
			return ITransactions.ResultType(0);
		}
	}

	/**
	 * @notice Retrieves the current status of a transaction.
	 * @param _tx_id The transaction identifier.
	 * @return ITransactions.TransactionStatus The transaction status.
	 */
	function getTransactionStatus(
		bytes32 _tx_id
	) external view returns (ITransactions.TransactionStatus) {
		// This will change in future releases
		return transactions.getTransaction(_tx_id).status;
	}

	/**
	 * @notice Checks if a transaction has on acceptance messages attached.
	 * @param _tx_id The transaction identifier.
	 * @return bool True if on acceptance messages exist, false otherwise.
	 */
	function hasTransactionOnAcceptanceMessages(
		bytes32 _tx_id
	) external view returns (bool) {
		return transactions.getTransaction(_tx_id).onAcceptanceMessages;
	}

	/**
	 * @notice Checks if a transaction has messages attached on finalization.
	 * @param _tx_id The transaction identifier.
	 * @return bool True if finalization messages exist, false otherwise.
	 */
	function hasTransactionOnFinalizationMessages(
		bytes32 _tx_id
	) external view returns (bool) {
		return transactions.hasMessagesOnFinalization(_tx_id);
	}

	/**
	 * @notice Retrieves all submitted messages for a transaction.
	 * @param _tx_id The transaction identifier.
	 * @return IMessages.SubmittedMessage[] The list of submitted messages.
	 */
	function getMessagesForTransaction(
		bytes32 _tx_id
	) external view returns (IMessages.SubmittedMessage[] memory) {
		return transactions.getMessagesForTransaction(_tx_id);
	}

	function getReadStateBlockRangeForTransaction(
		bytes32 _tx_id
	)
		external
		view
		returns (
			uint256 activationBlock,
			uint256 processingBlock,
			uint256 proposalBlock
		)
	{
		ITransactions.Transaction memory transaction = transactions
			.getTransaction(_tx_id);
		activationBlock = transaction.readStateBlockRange.activationBlock;
		processingBlock = transaction.readStateBlockRange.processingBlock;
		proposalBlock = transaction.readStateBlockRange.proposalBlock;
	}

	/**
	 * @notice Retrieves a paginated list of latest accepted transactions for a specific recipient
	 * @param recipient The address of the recipient contract
	 * @param startIndex The starting index for pagination (0-based)
	 * @param pageSize The maximum number of transactions to return
	 * @return TransactionData[] Array of transaction data objects, ordered by acceptance time (newest first)
	 * @dev Returns an empty array if startIndex is out of bounds
	 */
	function getLatestAcceptedTransactions(
		address recipient,
		uint256 startIndex,
		uint256 pageSize
	) external view returns (TransactionData[] memory) {
		// (Assumes that the Queues contract has been extended with a getter function
		// to return the accepted tx id at a given slot and to return the total count.)
		uint256 totalAccepted = queues.getAcceptedCount(recipient);
		if (startIndex >= totalAccepted) {
			return new TransactionData[](0);
		}
		uint256 endIndex = startIndex + pageSize;
		if (endIndex > totalAccepted) {
			endIndex = totalAccepted;
		}
		uint256 count = endIndex - startIndex;
		TransactionData[] memory results = new TransactionData[](count);
		for (uint256 i = startIndex; i < endIndex; i++) {
			bytes32 txId = queues.getAcceptedTxId(recipient, i);
			results[i - startIndex] = _getTransactionData(txId);
		}
		return results;
	}

	/**
	 * @notice Retrieves a paginated list of latest finalized transactions for a specific recipient
	 * @param recipient The address of the recipient contract
	 * @param startIndex The starting index for pagination (0-based)
	 * @param pageSize The maximum number of transactions to return
	 * @return TransactionData[] Array of transaction data objects, ordered by finalization time (newest first)
	 * @dev Returns an empty array if startIndex is out of bounds
	 */
	function getLatestFinalizedTransactions(
		address recipient,
		uint256 startIndex,
		uint256 pageSize
	) external view returns (TransactionData[] memory) {
		uint256 totalFinalized = queues.getFinalizedCount(recipient);
		if (startIndex >= totalFinalized) {
			return new TransactionData[](0);
		}
		uint256 endIndex = startIndex + pageSize;
		if (endIndex > totalFinalized) {
			endIndex = totalFinalized;
		}
		uint256 count = endIndex - startIndex;
		TransactionData[] memory results = new TransactionData[](count);
		for (uint256 i = startIndex; i < endIndex; i++) {
			bytes32 txId = queues.getFinalizedTxId(recipient, i);
			results[i - startIndex] = _getTransactionData(txId);
		}
		return results;
	}

	// ############################################
	// ########## INTERNAL FUNCTIONS ##############
	// ############################################

	function _getTransactionData(
		bytes32 _tx_id
	) internal view returns (TransactionData memory txData) {
		ITransactions.Transaction memory transaction = transactions
			.getTransaction(_tx_id);
		uint lastRound = transaction.roundData.length > 0
			? transaction.roundData.length - 1
			: 0;
		ITransactions.RoundData memory lastRoundData = transaction
			.roundData
			.length > 0
			? transaction.roundData[lastRound]
			: ITransactions.RoundData(
				0,
				0,
				0,
				0,
				0,
				0,
				ITransactions.ResultType(0),
				new address[](0),
				new bytes32[](0),
				new ITransactions.VoteType[](0)
			);
		txData = TransactionData({
			// Basic transaction info
			sender: transaction.sender,
			recipient: transaction.recipient,
			numOfInitialValidators: transaction.numOfInitialValidators,
			txSlot: transaction.txSlot,
			timestamp: transaction.timestamps.created,
			lastVoteTimestamp: transaction.timestamps.lastVote,
			randomSeed: transaction.randomSeed != bytes32(0)
				? transaction.randomSeed
				: consensusMain.contracts().genManager.recipientRandomSeed(
					transaction.recipient
				),
			result: lastRoundData.result,
			txData: transaction.txData,
			txReceipt: transaction.txReceipt,
			messages: transaction.messages,
			// Validator info
			validators: lastRoundData.roundValidators,
			validatorVotesHash: lastRoundData.validatorVotesHash,
			validatorVotes: lastRoundData.validatorVotes,
			// Queue info
			queueType: queues.getTransactionQueueType(_tx_id),
			queuePosition: queues.getTransactionQueuePosition(_tx_id),
			// Status info
			activator: transaction.activator,
			leader: lastRoundData.roundValidators.length > 0
				? lastRoundData.roundValidators[lastRoundData.leaderIndex]
				: address(0),
			status: transaction.status,
			committedVotesCount: lastRoundData.votesCommitted,
			revealedVotesCount: lastRoundData.votesRevealed,
			rotationsLeft: lastRoundData.rotationsLeft
		});
	}

	// ############################################
	// ################# SETTERS ##################
	// ############################################

	/**
	 * @notice Sets a new address for the transactions contract.
	 * @param _transactions The new transactions contract address.
	 */
	function setTransactions(address _transactions) external onlyOwner {
		transactions = ITransactions(_transactions);
	}

	/**
	 * @notice Sets a new address for the queues contract.
	 * @param _queues The new queues contract address.
	 */
	function setQueues(address _queues) external onlyOwner {
		queues = IQueues(_queues);
	}

	/**
	 * @notice Sets a new address for the consensusMain contract.
	 * @param _consensusMain The new consensusMain contract address.
	 */
	function setConsensusMain(address _consensusMain) external onlyOwner {
		consensusMain = IConsensusMain(_consensusMain);
	}

	// function getLatestIdleTransactionsInfo(
	// 	uint256 page
	// ) external pure returns (ITransactions.IdleTransactionInfo[] memory) {
	// 	// TODO:
	// }
}