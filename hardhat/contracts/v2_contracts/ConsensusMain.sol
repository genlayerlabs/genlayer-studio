// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { Initializable } from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import { Ownable2StepUpgradeable } from "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import { ReentrancyGuardUpgradeable } from "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import { AccessControlUpgradeable } from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";

import { Errors } from "./utils/Errors.sol";
import { IConsensusMain } from "./interfaces/IConsensusMain.sol";
import { IGenManager } from "./interfaces/IGenManager.sol";
import { ITransactions } from "./transactions/interfaces/ITransactions.sol";
import { IIdleness } from "./transactions/interfaces/IIdleness.sol";
import { IMessages } from "./interfaces/IMessages.sol";
import { IQueues } from "./interfaces/IQueues.sol";
import { IGenStaking } from "./interfaces/IGenStaking.sol";
import { IGhostFactory } from "./interfaces/IGhostFactory.sol";
/**
 * @title ConsensusMain
 * @notice Main contract for managing transaction consensus and validation in the Genlayer protocol
 * @dev Handles transaction lifecycle, issues messages, manages appeals, and handles fees
 */
contract ConsensusMain is
	Initializable,
	Ownable2StepUpgradeable,
	ReentrancyGuardUpgradeable,
	AccessControlUpgradeable
{
	/// @notice Consolidated external contract addresses used in ConsensusMain
	IConsensusMain.ExternalContracts public contracts;

	/// @notice Mapping of ghost contracts
	mapping(address addr => bool isGhost) public ghostContracts;

	/// @notice Gap for future upgrades
	uint256[50] private __gap;

	// EVENTS
	// In: addTransaction
	// Transition: Pending -> Active
	event NewTransaction(
		bytes32 indexed txId,
		address indexed recipient,
		address indexed activator
	);

	event TransactionLeaderRotated(
		bytes32 indexed txId,
		address indexed newLeader
	);
	// In: activateTransaction
	// Transition: Active -> Proposing
	event TransactionActivated(
		bytes32 indexed txId,
		address indexed leader,
		address[] validators
	);
	// In: proposeReceipt
	// Transition: Proposing -> Committing
	event TransactionReceiptProposed(bytes32 indexed tx_id);
	event TransactionLeaderTimeout(bytes32 indexed tx_id);

	// In: commitVote
	// Transition: Committing -> (ifLastVote) Revealing
	event VoteCommitted(
		bytes32 indexed txId,
		address indexed validator,
		bool isLastVote
	);
	// In: commitVote
	// Transition: Revealing -> (ifLastVote) ReadyToFinalize
	event VoteRevealed(
		bytes32 indexed txId,
		address indexed validator,
		ITransactions.VoteType voteType,
		bool isLastVote,
		ITransactions.ResultType result
	);
	// In: revealVote
	// Transition: Revealing (ifLastVote) -> ReadyToFinalize
	event TransactionAccepted(bytes32 indexed tx_id);

	// In: finalizeTransaction
	// Transition: ReadyToFinalize -> Finalized
	event TransactionFinalized(bytes32 indexed tx_id);
	event TransactionNeedsRecomputation(bytes32[] tx_ids);
	event AppealStarted(
		bytes32 indexed txId,
		address indexed appealer,
		uint256 appealBond,
		address[] appealValidators
	);
	event InternalMessageProcessed(
		bytes32 indexed txId,
		address indexed recipient,
		address indexed activator
	);
	event TransactionCancelled(bytes32 indexed txId, address indexed sender);
	event TransactionIdleValidatorReplacementFailed(
		bytes32 indexed txId,
		uint256 indexed validatorIndex
	);
	event TransactionIdleValidatorReplaced(
		bytes32 indexed txId,
		address indexed oldValidator,
		address indexed newValidator
	);
	event ExternalContractsSet(
		address ghostFactory,
		address genManager,
		address genTransactions,
		address genQueue,
		address genStaking,
		address genMessages,
		address idleness
	);

	struct internalMessageData {
		address sender;
		address recipient;
		bytes data;
	}

	/**
	 * @notice Initializes the contract
	 */
	function initialize() public initializer {
		__Ownable2Step_init();
		__Ownable_init(msg.sender);
		__ReentrancyGuard_init();
		__AccessControl_init();
	}

	// EXTERNAL FUNCTIONS - STATE CHANGING

	function emitTransactionLeaderRotated(
		bytes32 txId,
		address newLeader
	) external {
		emit TransactionLeaderRotated(txId, newLeader);
	}

	function emitTransactionActivated(
		bytes32 txId,
		address leader,
		address[] calldata validators
	) external {
		emit TransactionActivated(txId, leader, validators);
	}

	function emitTransactionReceiptProposed(
		bytes32 txId
	) external {
		emit TransactionReceiptProposed(txId);
	}

	function emitTransactionLeaderTimeout(
		bytes32 txId
	) external {
		emit TransactionLeaderTimeout(txId);
	}

	function emitVoteCommitted(
		bytes32 txId,
		address validator,
		bool isLastVote
	) external {
		emit VoteCommitted(txId, validator, isLastVote);
	}

	function emitVoteRevealed(
		bytes32 txId,
		address validator,
		ITransactions.VoteType voteType,
		bool isLastVote,
		ITransactions.ResultType result
	) external {
		emit VoteRevealed(txId, validator, voteType, isLastVote, result);
	}

	function _processInternalMessages(internalMessageData[] calldata internalMessages) internal {
		for (uint256 i = 0; i < internalMessages.length; i++) {
			if (!ghostContracts[internalMessages[i].recipient]) {
				_storeGhost(internalMessages[i].recipient);
			}
			(
				bytes32 generated_txId,
				address newActivator
			) = _addTransaction(
				internalMessages[i].sender,
				internalMessages[i].recipient,
				5, // or pass as part of internalMessages.data
				0, // or pass as part of internalMessages.data
				internalMessages[i].data
			);
			emit InternalMessageProcessed(
				generated_txId,
				internalMessages[i].recipient,
				newActivator
			);
		}
	}

	function emitTransactionAccepted(
		bytes32 txId,
		internalMessageData[] calldata internalMessages
	) external {
		if (internalMessages.length > 0) {
			_processInternalMessages(internalMessages);
		}
		emit TransactionAccepted(txId);
	}

	function emitTransactionFinalized(
		bytes32 txId,
		internalMessageData[] calldata internalMessages
	) external {
		if (internalMessages.length > 0) {
			_processInternalMessages(internalMessages);
		}
		emit TransactionFinalized(txId);
	}

	function emitAppealStarted(
		bytes32 txId,
		address appealer,
		uint256 appealBond,
		address[] calldata appealValidators
	) external {
		emit AppealStarted(txId, appealer, appealBond, appealValidators);
	}

	/**
	 * @notice Adds a new transaction to the system
	 * @param _sender Transaction sender address
	 * @param _recipient Recipient GenVM contract address
	 * @param _numOfInitialValidators Number of validators to assign
	 * @param _maxRotations Maximum number of leader rotations allowed
	 * @param _txData Transaction data to be executed
	 */
	function addTransaction(
		address _sender,
		address _recipient,
		uint256 _numOfInitialValidators,
		uint256 _maxRotations,
		bytes memory _txData
	) external {
		if (_txData.length > 0) {
			_addTransaction(
				_sender,
				_recipient,
				_numOfInitialValidators,
				_maxRotations,
				_txData
			);
		} else {
			revert Errors.EmptyTransaction();
		}
		// TODO: Fee verification handling
	}

	/**
	 * @notice Activates a pending transaction by updating its random seed and moving it to the active state
	 * @dev Only callable by the designated activator for the transaction
	 * @param _txId The unique identifier of the transaction to activate
	 * @param _vrfProof Verifiable random function proof used to update the random seed
	 * @custom:throws TransactionNotAtPendingQueueHead if transaction is not at the head of pending queue
	 * @custom:emits TransactionActivated when transaction is successfully activated
	 */
	function activateTransaction(
		bytes32 _txId,
		bytes calldata _vrfProof
	) external {
		ITransactions.Transaction memory transaction = contracts
			.genTransactions
			.getTransaction(_txId);
		address recipient = transaction.recipient;
		if (!contracts.genQueue.isAtPendingQueueHead(recipient, _txId)) {
			revert Errors.TransactionNotAtPendingQueueHead();
		}
		bytes32 randomSeed = contracts.genManager.updateRandomSeedForRecipient(
			recipient,
			msg.sender,
			_vrfProof
		);
		_activateTransaction(_txId, randomSeed);
	}

	/**
	 * @notice Proposes a transaction receipt and associated messages for a transaction
	 * @dev Only callable by the current leader validator for the transaction
	 * @param _txId The unique identifier of the transaction
	 * @param _txReceipt The execution receipt/result of the transaction
	 * @param _messages Array of messages to be emitted as part of the transaction
	 * @param _vrfProof Verifiable random function proof used to update the random seed
	 * @custom:throws TransactionNotProposing if transaction is not in Proposing state
	 * @custom:emits TransactionReceiptProposed when receipt is successfully proposed
	 */
	function proposeReceipt(
		bytes32 _txId,
		bytes calldata _txReceipt,
		uint256 _processingBlock,
		IMessages.SubmittedMessage[] calldata _messages,
		bytes calldata _vrfProof
	) external {
		(
			address recipient,
			bool leaderFailedToPropose,
			address newLeader,

		) = contracts.genTransactions.proposeTransactionReceipt(
				_txId,
				msg.sender,
				_processingBlock,
				_txReceipt,
				_messages
			);

		contracts.genManager.updateRandomSeedForRecipient(
			recipient,
			msg.sender,
			_vrfProof
		);

		if (leaderFailedToPropose) {
			if (newLeader != address(0)) {
				emit TransactionLeaderRotated(_txId, newLeader);
			} else {
				emit TransactionLeaderTimeout(_txId);
			}
		} else {
			emit TransactionReceiptProposed(_txId);
		}
	}

	/**
	 * @notice Commits a vote for a transaction
	 * @dev Only callable by a validator for the transaction
	 * @param _txId The unique identifier of the transaction
	 * @param _commitHash The hash of the vote to commit
	 * @custom:throws TransactionNotAppealCommitting if transaction is not in AppealCommitting state
	 * @custom:throws TransactionNotCommitting if transaction is not in Committing state
	 * @custom:throws VoteAlreadyCommitted if the validator has already committed a vote
	 * @custom:emits VoteCommitted when vote is successfully committed
	 */
	function commitVote(bytes32 _txId, bytes32 _commitHash) external {
		bool isLastVote = contracts.genTransactions.commitVote(
			_txId,
			_commitHash,
			msg.sender
		);
		emit VoteCommitted(_txId, msg.sender, isLastVote);
	}

	/**
	 * @notice Reveals a vote for a transaction
	 * @dev Only callable by a validator for the transaction
	 * @param _txId The unique identifier of the transaction
	 * @param _voteHash The hash of the vote to reveal
	 * @param _voteType The type of the vote
	 * @param _nonce The nonce used for the vote
	 * @custom:throws TransactionNotAppealRevealing if transaction is not in AppealRevealing state
	 * @custom:throws TransactionNotAppealRevealing if transaction is not in AppealRevealing state
	 * @custom:throws TransactionNotRevealing if transaction is not in Revealing state
	 * @custom:throws InvalidVote if the revealed vote is invalid
	 * @custom:emits VoteRevealed when vote is successfully revealed
	 */
	function revealVote(
		bytes32 _txId,
		bytes32 _voteHash,
		ITransactions.VoteType _voteType,
		uint256 _nonce
	) external {
		if (
			keccak256(abi.encodePacked(msg.sender, _voteType, _nonce)) !=
			_voteHash
		) {
			revert Errors.InvalidVote();
		}
		(
			bool isLastVote,
			ITransactions.ResultType result,
			address recipient, // used with fees
			,
			/*uint256 round*/ bool hasMessagesOnAcceptance,
			uint256 rotationsLeft,
			ITransactions.NewRoundData memory newRoundData
		) = contracts.genTransactions.revealVote(
				_txId,
				_voteHash,
				_voteType,
				msg.sender
			);
		if (isLastVote) {
			if (newRoundData.round > 0) {
				(, bytes32[] memory txsForRecomputation) = contracts
					.genQueue
					.addTransactionToPendingQueue(recipient, _txId);
				emit TransactionNeedsRecomputation(txsForRecomputation);
				emit TransactionActivated(
					_txId,
					newRoundData.roundValidators[newRoundData.leaderIndex],
					newRoundData.roundValidators
				);
			} else if (
				result == ITransactions.ResultType.MajorityAgree ||
				result == ITransactions.ResultType.Agree
			) {
				contracts.genQueue.addTransactionToAcceptedQueue(
					recipient,
					_txId
				);
				emit TransactionAccepted(_txId);

				// Process on-acceptance messages
				if (hasMessagesOnAcceptance) {
					_processMessages(_txId, true); // true for acceptance phase
				}
			} else {
				if (rotationsLeft > 0) {
					// (more rounds of rotations) Rotate
					// TODO: check also gas limit
					_rotateLeader(_txId);
				} else {
					contracts.genQueue.addTransactionToUndeterminedQueue(
						recipient,
						_txId
					);
				}
			}
		}
		emit VoteRevealed(_txId, msg.sender, _voteType, isLastVote, result);
	}

	/**
	 * @notice Finalizes a transaction
	 * @dev Only callable by the transaction recipient
	 * @param _txId The unique identifier of the transaction
	 * @custom:throws TransactionNotAcceptedOrUndetermined if transaction is not in Accepted or Undetermined state
	 * @custom:emits TransactionFinalized when transaction is successfully finalized
	 */
	function finalizeTransaction(bytes32 _txId) external {
		(address recipient, uint256 lastVoteTimestamp) = contracts
			.genTransactions
			.finalizeTransaction(_txId);
		if (!contracts.genQueue.isAtFinalizedQueueHead(recipient, _txId)) {
			revert Errors.FinalizationNotAllowed();
		}
		contracts.genQueue.addTransactionToFinalizedQueue(recipient, _txId);
		if (
			block.timestamp - lastVoteTimestamp >
			contracts.idleness.getTimeouts().accept
		) {
			if (contracts.genTransactions.hasMessagesOnFinalization(_txId)) {
				_processMessages(_txId, false); // false for finalization phase
			}
			emit TransactionFinalized(_txId);
		}
	}

	/**
	 * @notice Submits an appeal for a transaction
	 * @dev Only callable by a validator for the transaction
	 * @param _txId The unique identifier of the transaction
	 * @custom:throws CanNotAppeal if the appeal cannot be submitted
	 * @custom:emits AppealStarted when appeal is successfully started
	 */
	function submitAppeal(bytes32 _txId) external payable {
		IQueues.LastQueueModification memory lastQueueModification = contracts
			.genQueue
			.getLastQueueModification(_txId);
		if (
			lastQueueModification.lastQueueTimestamp > 0 &&
			block.timestamp <
			lastQueueModification.lastQueueTimestamp +
				contracts.idleness.getTimeouts().activate &&
			lastQueueModification.lastQueueType == IQueues.QueueType.Pending
		) {
			revert Errors.CanNotAppeal();
		}

		(address[] memory appealValidators, ) = contracts
			.genTransactions
			.submitAppeal(_txId, msg.value);
		// uint256 numOfValidatorsForAppealRound = feeManager.addAppealRound(_txId);
		emit AppealStarted(_txId, msg.sender, msg.value, appealValidators);
	}

	/**
	 * @notice Executes a message
	 * @dev Only callable by the messages contract or the owner
	 * @param _recipient The address of the recipient
	 * @param _value The value to be sent
	 * @param _data The data to be executed
	 * @return success - true if the message is successfully executed, false otherwise
	 */
	function executeMessage(
		address _recipient,
		uint256 _value,
		bytes memory _data
	) external returns (bool success) {
		if (
			msg.sender != address(contracts.genMessages) &&
			msg.sender != owner()
		) {
			revert Errors.CallerNotMessages();
		}
		(success, ) = _recipient.call{ value: _value }(_data);
	}

	/**
	 * @notice Cancels a pending transaction
	 * @param _txId The unique identifier of the transaction to cancel
	 * @dev Only the original sender can cancel a pending transaction
	 * @custom:throws TransactionNotPending if transaction is not in pending state
	 * @custom:throws TransactionNotAtPendingQueueHead if transaction is not at the head of pending queue
	 * @custom:throws CallerNotSender if caller is not the original transaction sender
	 * @custom:emits TransactionCancelled when transaction is successfully cancelled
	 */
	function cancelTransaction(bytes32 _txId) external nonReentrant {
		address recipient = contracts.genTransactions.cancelTransaction(
			_txId,
			msg.sender
		);
		contracts.genQueue.removeTransactionFromPendingQueue(recipient, _txId);
		emit TransactionCancelled(_txId, msg.sender);
	}

	// SETTERS

	/**
	 * @notice Sets all the external contract addresses at once
	 * @param _ghostFactory Address of the ghost factory contract.
	 * @param _genManager Address of the Gen Manager contract.
	 * @param _genTransactions Address of the Gen Transactions contract.
	 * @param _genQueue Address of the Gen Queue contract.
	 * @param _genStaking Address of the Gen Staking contract.
	 * @param _genMessages Address of the Gen Messages contract.
	 * @param _idleness Address of the Idleness contract.
	 */
	function setExternalContracts(
		address _ghostFactory,
		address _genManager,
		address _genTransactions,
		address _genQueue,
		address _genStaking,
		address _genMessages,
		address _idleness
	) external onlyOwner {
		_checkAddress(_ghostFactory);
		_checkAddress(_genManager);
		_checkAddress(_genTransactions);
		_checkAddress(_genQueue);
		_checkAddress(_genStaking);
		_checkAddress(_genMessages);
		_checkAddress(_idleness);

		contracts.ghostFactory = IGhostFactory(_ghostFactory);
		contracts.genManager = IGenManager(_genManager);
		contracts.genTransactions = ITransactions(_genTransactions);
		contracts.genQueue = IQueues(_genQueue);
		contracts.genStaking = IGenStaking(_genStaking);
		contracts.genMessages = IMessages(_genMessages);
		contracts.idleness = IIdleness(_idleness);

		emit ExternalContractsSet(
			_ghostFactory,
			_genManager,
			_genTransactions,
			_genQueue,
			_genStaking,
			_genMessages,
			_idleness
		);
	}

	// INTERNAL FUNCTIONS

	function _addTransaction(
		address _sender,
		address _recipient,
		uint256 _numOfInitialValidators,
		uint256 _maxRotations,
		bytes memory _txData
	) internal returns (bytes32 txId, address activator) {
		if (_sender == address(0)) {
			_sender = msg.sender;
		}
		bytes32 randomSeed = _recipient == address(0)
			? keccak256(abi.encodePacked(_sender))
			: contracts.genManager.recipientRandomSeed(_recipient);
		if (_recipient == address(0)) {
			// Contract deployment transaction
			contracts.ghostFactory.createGhost();
			address ghost = contracts.ghostFactory.latestGhost();
			_storeGhost(ghost);
			_recipient = ghost;
			// Initial random seed for the recipient account
			contracts.genManager.addNewRandomSeedForRecipient(
				_recipient,
				randomSeed
			);
		} else if (!ghostContracts[_recipient]) {
			revert Errors.NonGenVMContract();
		}
		(txId, activator) = _generateTx(
			_sender,
			_recipient,
			_numOfInitialValidators,
			_maxRotations,
			randomSeed,
			_txData
		);
		emit NewTransaction(txId, _recipient, activator);
	}

	/**
	 * @notice Rotates the leader for a transaction
	 * @param _txId The unique identifier of the transaction
	 */
	function _rotateLeader(bytes32 _txId) internal {
		address newLeader = contracts.genTransactions.rotateLeader(_txId);
		emit TransactionLeaderRotated(_txId, newLeader);
	}

	/**
	 * @notice Stores a ghost contract address
	 * @param _ghost Address of the ghost contract
	 */
	function _storeGhost(address _ghost) internal {
		ghostContracts[_ghost] = true;
	}

	/**
	 * @notice Generates a new transaction
	 * @param _sender Transaction sender address
	 * @param _recipient Recipient GenVM contract address
	 * @param _numOfInitialValidators Number of validators to assign
	 * @param _randomSeed Random seed for the transaction
	 * @param _txData Transaction data to be executed
	 */
	function _generateTx(
		address _sender,
		address _recipient,
		uint256 _numOfInitialValidators,
		uint256 /*_maxRotations*/, // used with fees
		bytes32 _randomSeed,
		bytes memory _txData
	) internal returns (bytes32 txId, address activator) {
		txId = keccak256(
			abi.encodePacked(_recipient, block.timestamp, _randomSeed)
		);
		(uint256 txSlot, ) = contracts.genQueue.addTransactionToPendingQueue(
			_recipient,
			txId
		);
		activator = _getActivatorForTx(_randomSeed, txSlot);
		contracts.genTransactions.addNewTransaction(
			txId,
			ITransactions.Transaction({
				id: txId,
				sender: _sender,
				recipient: _recipient,
				numOfInitialValidators: _numOfInitialValidators,
				txSlot: txSlot,
				activator: activator,
				status: ITransactions.TransactionStatus.Pending,
				previousStatus: ITransactions.TransactionStatus.Uninitialized,
				timestamps: ITransactions.Timestamps({
					created: block.timestamp,
					pending: block.timestamp,
					activated: 0,
					proposed: 0,
					committed: 0,
					lastVote: 0
				}),
				randomSeed: _randomSeed,
				onAcceptanceMessages: false,
				result: ITransactions.ResultType(0),
				readStateBlockRange: ITransactions.ReadStateBlockRange({
					activationBlock: 0,
					processingBlock: 0,
					proposalBlock: 0
				}),
				txData: _txData,
				txReceipt: new bytes(0),
				messages: new IMessages.SubmittedMessage[](0),
				consumedValidators: new address[](0),
				roundData: new ITransactions.RoundData[](0)
			})
		);
	}

	/**
	 * @notice Activates a pending transaction
	 * @param _txId The unique identifier of the transaction
	 * @param _randomSeed Random seed for the transaction
	 */
	function _activateTransaction(bytes32 _txId, bytes32 _randomSeed) internal {
		(
			address recepient,
			uint256 leaderIndex,
			address[] memory validators
		) = contracts.genTransactions.activateTransaction(
				_txId,
				msg.sender,
				_randomSeed
			);
		if (!contracts.genQueue.isAtPendingQueueHead(recepient, _txId)) {
			revert Errors.TransactionNotAtPendingQueueHead();
		}
		emit TransactionActivated(_txId, validators[leaderIndex], validators);
	}

	/**
	 * @notice Retrieves the activator for a transaction
	 * @param _randomSeed Random seed for the transaction
	 * @param _randomIndex Random index for the transaction
	 * @return validator - the address of the validator that will activate the transaction
	 */
	function _getActivatorForTx(
		bytes32 _randomSeed,
		uint256 _randomIndex
	) internal view returns (address validator) {
		bytes32 combinedSeed = keccak256(
			abi.encodePacked(_randomSeed, _randomIndex)
		);
		validator = contracts.genStaking.getActivatorForSeed(combinedSeed);
	}

	/**
	 * @notice Processes messages for a transaction
	 * @param _txId The unique identifier of the transaction
	 * @param isAcceptance Whether the messages are for acceptance or finalization
	 */
	function _processMessages(bytes32 _txId, bool isAcceptance) internal {
		IMessages.SubmittedMessage[] memory messages = contracts
			.genTransactions
			.getMessagesForTransaction(_txId);

		for (uint256 i = 0; i < messages.length; i++) {
			IMessages.SubmittedMessage memory message = messages[i];

			// Only process messages for the current phase (acceptance or finalization)
			if (message.onAcceptance != isAcceptance) {
				continue;
			}
			if (message.messageType == IMessages.MessageType.External) {
				// Existing external message handling
				contracts.genMessages.executeMessage(message);
			} else if (message.messageType == IMessages.MessageType.Internal) {
				// Get transaction sender (the ghost contract that issued the original transaction)
				address recipient = contracts
					.genTransactions
					.getTransactionRecipient(_txId);

				// Create new transaction from current ghost to target ghost
				(
					bytes32 generated_txId,
					address newActivator
				) = _addTransaction(
						recipient, // sender (current ghost)
						message.recipient, // recipient (target ghost)
						5, // or pass as part of message.data
						0, // or pass as part of message.data
						message.data // transaction data
					);
				emit InternalMessageProcessed(
					generated_txId,
					message.recipient,
					newActivator
				);
			}
		}
	}

	/**
	 * @notice Checks if an address is valid
	 * @param _address The address to check
	 */
	function _checkAddress(address _address) internal pure {
		if (_address == address(0)) {
			revert Errors.InvalidAddress();
		}
	}
}