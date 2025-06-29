// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import "./interfaces/IGenManager.sol";
import "./transactions/interfaces/ITransactions.sol";
import "./interfaces/IQueues.sol";
import "./interfaces/IGhostFactory.sol";
import "./interfaces/IGenStaking.sol";
import "./interfaces/IMessages.sol";
import "./interfaces/IFeeManager.sol";
import "./utils/RandomnessUtils.sol";
import "./utils/Errors.sol";
/**
 * @title ConsensusMain
 * @notice Main contract for managing transaction consensus and validation in the Genlayer protocol
 * @dev Handles transaction lifecycle, issues messages, manages appeals, and handles fees
 */
contract ConsensusMainWithFees is
	Initializable,
	Ownable2StepUpgradeable,
	ReentrancyGuardUpgradeable,
	AccessControlUpgradeable
{
	bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");
	bytes32 public constant VALIDATOR_ROLE = keccak256("VALIDATOR_ROLE");
	uint256 public ACCEPTANCE_TIMEOUT;
	uint256 public ACTIVATION_TIMEOUT;

	IGenManager public genManager;
	ITransactions public genTransactions;
	IQueues public genQueue;
	IGhostFactory public ghostFactory;
	IGenStaking public genStaking;
	IMessages public genMessages;
	IFeeManager public feeManager;

	/// move to Manager
	mapping(address => bool) public ghostContracts;

	receive() external payable {}

	/**
	 * @notice Initializes the contract
	 * @param _genManager Address of the Gen Manager contract
	 */
	function initialize(address _genManager) public initializer {
		__Ownable2Step_init();
		__Ownable_init(msg.sender);
		__ReentrancyGuard_init();
		__AccessControl_init();
		genManager = IGenManager(_genManager);
	}

	// EXTERNAL FUNCTIONS - STATE CHANGING

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
		bytes memory _txData,
		IFeeManager.FeesDistribution memory _feesDistribution
	) external payable nonReentrant {
		if (_txData.length == 0) {
			revert Errors.EmptyTransaction();
		}
		uint totalFeesToPay = feeManager.calculateRoundFees(
			bytes32(0),
			_feesDistribution,
			_numOfInitialValidators,
			0
		);
		if (totalFeesToPay > msg.value) {
			revert Errors.InsufficientFees();
		}
		(bytes32 _txId, ) = _addTransaction(
			_sender,
			_recipient,
			_numOfInitialValidators,
			_maxRotations,
			_txData
		);
		feeManager.topUpFees(
			_txId,
			_feesDistribution,
			msg.value,
			false,
			_sender
		);
	}

	function topUpFees(
		bytes32 _txId,
		IFeeManager.FeesDistribution memory _feesDistribution
	) external payable nonReentrant {
		if (msg.value == 0) {
			revert Errors.InsufficientFees();
		}
		feeManager.topUpFees(
			_txId,
			_feesDistribution,
			msg.value,
			true,
			msg.sender
		);
	}

	/**
	 * @notice Activates a pending transaction by updating its random seed and moving it to the active state
	 * @dev Only callable by the designated activator for the transaction
	 * @param _tx_id The unique identifier of the transaction to activate
	 * @param _vrfProof Verifiable random function proof used to update the random seed
	 * @custom:throws TransactionNotAtPendingQueueHead if transaction is not at the head of pending queue
	 * @custom:emits TransactionActivated when transaction is successfully activated
	 */
	function activateTransaction(
		bytes32 _tx_id,
		bytes calldata _vrfProof
	) external {
		address recipient = genTransactions.getTransactionRecipient(_tx_id);
		if (!genQueue.isAtPendingQueueHead(recipient, _tx_id)) {
			revert Errors.TransactionNotAtPendingQueueHead();
		}
		bytes32 randomSeed = genManager.updateRandomSeedForRecipient(
			recipient,
			msg.sender,
			_vrfProof
		);
		_activateTransaction(_tx_id, randomSeed);
	}

	/**
	 * @notice Proposes a transaction receipt and associated messages for a transaction
	 * @dev Only callable by the current leader validator for the transaction
	 * @param _tx_id The unique identifier of the transaction
	 * @param _txReceipt The execution receipt/result of the transaction
	 * @param _messages Array of messages to be emitted as part of the transaction
	 * @param _vrfProof Verifiable random function proof used to update the random seed
	 * @custom:throws TransactionNotProposing if transaction is not in Proposing state
	 * @custom:emits TransactionReceiptProposed when receipt is successfully proposed
	 */
	function proposeReceipt(
		bytes32 _tx_id,
		bytes calldata _txReceipt,
		uint256 _processingBlock,
		IMessages.SubmittedMessage[] calldata _messages,
		bytes calldata _vrfProof
	) external {
		(
			address recipient,
			bool leaderTimeout,
			address newLeader,
			uint round
		) = genTransactions.proposeTransactionReceipt(
				_tx_id,
				msg.sender,
				_processingBlock,
				_txReceipt,
				_messages
			);

		genManager.updateRandomSeedForRecipient(
			recipient,
			msg.sender,
			_vrfProof
		);
		feeManager.recordProposedReceipt(
			_tx_id,
			round,
			msg.sender,
			leaderTimeout
		);
		if (leaderTimeout) {
			if (newLeader != address(0)) {
				emit TransactionLeaderRotated(_tx_id, newLeader);
			} else {
				emit TransactionLeaderTimeout(_tx_id);
			}
		} else {
			emit TransactionReceiptProposed(_tx_id);
		}
	}

	/**
	 * @notice Commits a vote for a transaction
	 * @dev Only callable by a validator for the transaction
	 * @param _tx_id The unique identifier of the transaction
	 * @param _commitHash The hash of the vote to commit
	 * @custom:throws TransactionNotAppealCommitting if transaction is not in AppealCommitting state
	 * @custom:throws TransactionNotCommitting if transaction is not in Committing state
	 * @custom:throws VoteAlreadyCommitted if the validator has already committed a vote
	 * @custom:emits VoteCommitted when vote is successfully committed
	 */
	function commitVote(bytes32 _tx_id, bytes32 _commitHash) external {
		bool isLastVote = genTransactions.commitVote(
			_tx_id,
			_commitHash,
			msg.sender
		);
		emit VoteCommitted(_tx_id, msg.sender, isLastVote);
	}

	/**
	 * @notice Reveals a vote for a transaction
	 * @dev Only callable by a validator for the transaction
	 * @param _tx_id The unique identifier of the transaction
	 * @param _voteHash The hash of the vote to reveal
	 * @param _voteType The type of the vote
	 * @param _nonce The nonce used for the vote
	 * @custom:throws TransactionNotAppealRevealing if transaction is not in AppealRevealing state
	 * @custom:throws TransactionNotRevealing if transaction is not in Revealing state
	 * @custom:throws InvalidVote if the revealed vote is invalid
	 * @custom:emits VoteRevealed when vote is successfully revealed
	 */
	function revealVote(
		bytes32 _tx_id,
		bytes32 _voteHash,
		ITransactions.VoteType _voteType,
		uint _nonce
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
			address recipient,
			uint round,
			bool hasMessagesOnAcceptance,
			uint rotationsLeft,
			ITransactions.NewRoundData memory newRoundData
		) = genTransactions.revealVote(
				_tx_id,
				_voteHash,
				_voteType,
				msg.sender
			);
		bool feesForNewRoundApproved = feeManager.recordRevealedVote(
			_tx_id,
			round,
			msg.sender,
			isLastVote,
			_voteType,
			result
		);
		if (isLastVote) {
			if (newRoundData.round > 0 && feesForNewRoundApproved) {
				(, bytes32[] memory txsForRecomputation) = genQueue
					.addTransactionToPendingQueue(recipient, _tx_id);
				emit TransactionNeedsRecomputation(txsForRecomputation);
				emit TransactionActivated(
					_tx_id,
					newRoundData.roundValidators[newRoundData.leaderIndex],
					newRoundData.roundValidators
				);
			} else if (
				result == ITransactions.ResultType.MajorityAgree ||
				result == ITransactions.ResultType.Agree
			) {
				genQueue.addTransactionToAcceptedQueue(recipient, _tx_id);
				emit TransactionAccepted(_tx_id);

				// Process on-acceptance messages
				if (hasMessagesOnAcceptance) {
					_processMessages(_tx_id, true); // true for acceptance phase
				}
			} else {
				if (rotationsLeft > 0) {
					_rotateLeader(_tx_id);
				} else {
					genQueue.addTransactionToUndeterminedQueue(
						recipient,
						_tx_id
					);
				}
			}
		}
		emit VoteRevealed(_tx_id, msg.sender, _voteType, isLastVote, result);
	}

	/**
	 * @notice Finalizes a transaction
	 * @dev Only callable by the transaction recipient
	 * @param _tx_id The unique identifier of the transaction
	 * @custom:throws TransactionNotAcceptedOrUndetermined if transaction is not in Accepted or Undetermined state
	 * @custom:emits TransactionFinalized when transaction is successfully finalized
	 */
	function finalizeTransaction(bytes32 _tx_id) external {
		(address recipient, uint256 lastVoteTimestamp) = genTransactions
			.finalizeTransaction(_tx_id);
		if (!genQueue.isAtFinalizedQueueHead(recipient, _tx_id)) {
			revert Errors.FinalizationNotAllowed();
		}
		genQueue.addTransactionToFinalizedQueue(recipient, _tx_id);
		if (block.timestamp - lastVoteTimestamp < ACCEPTANCE_TIMEOUT) {
			revert Errors.TransactionCanNotBeFinalized();
		}
		feeManager.distributeFees(_tx_id);
		if (genTransactions.hasMessagesOnFinalization(_tx_id)) {
			_processMessages(_tx_id, false); // false for finalization phase
		}
		emit TransactionFinalized(_tx_id);
	}

	/**
	 * @notice Submits an appeal for a transaction
	 * @dev Only callable by a validator for the transaction
	 * @param _tx_id The unique identifier of the transaction
	 * @custom:throws CanNotAppeal if the appeal cannot be submitted
	 * @custom:emits AppealStarted when appeal is successfully started
	 */
	function submitAppeal(bytes32 _tx_id) external payable {
		(address[] memory appealValidators, uint round) = _submitAppeal(
			_tx_id,
			msg.value
		);
		bool feesForNewRoundApproved = feeManager.addAppealRound(
			_tx_id,
			round,
			msg.value,
			msg.sender
		);
		if (feesForNewRoundApproved) {
			emit AppealStarted(_tx_id, msg.sender, msg.value, appealValidators);
		}
	}

	/**
	 * @notice Submits an appeal for a transaction
	 * @dev Only callable by a validator for the transaction
	 * @param _tx_id The unique identifier of the transaction
	 * @custom:throws CanNotAppeal if the appeal cannot be submitted
	 * @custom:emits AppealStarted when appeal is successfully started
	 */
	function topUpAndSubmitAppeal(
		bytes32 _tx_id,
		IFeeManager.FeesDistribution memory _feesDistribution
	) external payable {
		(address[] memory appealValidators /*uint round*/, ) = _submitAppeal(
			_tx_id,
			msg.value
		);
		bool feesForNewRoundApproved = feeManager.topUpAndSubmitAppeal(
			_tx_id,
			_feesDistribution,
			msg.value,
			msg.sender,
			true
		);
		if (!feesForNewRoundApproved) {
			revert Errors.CanNotAppeal();
		}
		emit AppealStarted(_tx_id, msg.sender, msg.value, appealValidators);
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
		uint _value,
		bytes memory _data
	) external returns (bool success) {
		if (msg.sender != address(genMessages) && msg.sender != owner()) {
			revert Errors.CallerNotMessages();
		}
		(success, ) = _recipient.call{ value: _value }(_data);
	}

	/**
	 * @notice Cancels a pending transaction
	 * @param _tx_id The unique identifier of the transaction to cancel
	 * @dev Only the original sender can cancel a pending transaction
	 * @custom:throws TransactionNotPending if transaction is not in pending state
	 * @custom:throws TransactionNotAtPendingQueueHead if transaction is not at the head of pending queue
	 * @custom:throws CallerNotSender if caller is not the original transaction sender
	 * @custom:emits TransactionCancelled when transaction is successfully cancelled
	 */
	function cancelTransaction(bytes32 _tx_id) external {
		address recipient = genTransactions.cancelTransaction(
			_tx_id,
			msg.sender
		);
		genQueue.removeTransactionFromPendingQueue(recipient, _tx_id);
		emit TransactionCancelled(_tx_id, msg.sender);
	}

	// VIEW FUNCTIONS

	// INTERNAL FUNCTIONS
	function _addTransaction(
		address _sender,
		address _recipient,
		uint256 _numOfInitialValidators,
		uint256 _maxRotations,
		bytes memory _txData
	) internal returns (bytes32 tx_id, address activator) {
		if (_sender == address(0)) {
			_sender = msg.sender;
		}
		bytes32 randomSeed = _recipient == address(0)
			? keccak256(abi.encodePacked(_sender))
			: genManager.recipientRandomSeed(_recipient);
		if (_recipient == address(0)) {
			// Contract deployment transaction
			ghostFactory.createGhost();
			address ghost = ghostFactory.latestGhost();
			ghostContracts[ghost] = true;
			_recipient = ghost;
			// Initial random seed for the recipient account
			genManager.addNewRandomSeedForRecipient(_recipient, randomSeed);
		} else if (!ghostContracts[_recipient]) {
			revert Errors.NonGenVMContract();
		}
		// bytes32 randomSeed = recipientRandomSeed[_recipient]; // recipient randomSeed is used for activation
		(tx_id, activator) = _generateTx(
			_sender,
			_recipient,
			_numOfInitialValidators,
			_maxRotations,
			randomSeed,
			_txData
		);
		emit NewTransaction(tx_id, _recipient, activator);
	}

	/**
	 * @notice Rotates the leader for a transaction
	 * @param _tx_id The unique identifier of the transaction
	 */
	function _rotateLeader(bytes32 _tx_id) internal {
		address newLeader = genTransactions.rotateLeader(_tx_id);
		emit TransactionLeaderRotated(_tx_id, newLeader);
	}

	function _submitAppeal(
		bytes32 _tx_id,
		uint256 _appealBond
	) internal returns (address[] memory appealValidators, uint round) {
		IQueues.LastQueueModification memory lastQueueModification = genQueue
			.getLastQueueModification(_tx_id);
		if (
			lastQueueModification.lastQueueTimestamp > 0 &&
			block.timestamp <
			lastQueueModification.lastQueueTimestamp + ACTIVATION_TIMEOUT &&
			lastQueueModification.lastQueueType == IQueues.QueueType.Pending
		) {
			revert Errors.CanNotAppeal();
		}
		(appealValidators, round) = genTransactions.submitAppeal(
			_tx_id,
			_appealBond
		);
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
		uint256 /*_maxRotations*/,
		bytes32 _randomSeed,
		bytes memory _txData
	) internal returns (bytes32 tx_id, address activator) {
		tx_id = keccak256(
			abi.encodePacked(_recipient, block.timestamp, _randomSeed)
		);
		(uint256 txSlot, ) = genQueue.addTransactionToPendingQueue(
			_recipient,
			tx_id
		);
		activator = _getActivatorForTx(_randomSeed, txSlot);
		genTransactions.addNewTransaction(
			tx_id,
			ITransactions.Transaction({
				id: tx_id,
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
	 * @param _tx_id The unique identifier of the transaction
	 * @param _randomSeed Random seed for the transaction
	 */
	function _activateTransaction(
		bytes32 _tx_id,
		bytes32 _randomSeed
	) internal {
		(
			address recepient,
			uint256 leaderIndex,
			address[] memory validators
		) = genTransactions.activateTransaction(
				_tx_id,
				msg.sender,
				_randomSeed
			);
		if (!genQueue.isAtPendingQueueHead(recepient, _tx_id)) {
			revert Errors.TransactionNotAtPendingQueueHead();
		}
		emit TransactionActivated(_tx_id, validators[leaderIndex], validators);
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
		validator = genStaking.getActivatorForSeed(combinedSeed);
	}

	/**
	 * @notice Processes messages for a transaction
	 * @param _tx_id The unique identifier of the transaction
	 * @param isAcceptance Whether the messages are for acceptance or finalization
	 */
	function _processMessages(bytes32 _tx_id, bool isAcceptance) internal {
		IMessages.SubmittedMessage[] memory messages = genTransactions
			.getMessagesForTransaction(_tx_id);

		for (uint i = 0; i < messages.length; i++) {
			IMessages.SubmittedMessage memory message = messages[i];

			// Only process messages for the current phase (acceptance or finalization)
			if (message.onAcceptance != isAcceptance) {
				continue;
			}
			if (message.messageType == IMessages.MessageType.External) {
				// Existing external message handling
				genMessages.executeMessage(message);
			} else if (message.messageType == IMessages.MessageType.Internal) {
				// Get transaction sender (the ghost contract that issued the original transaction)
				address recipient = genTransactions.getTransactionRecipient(
					_tx_id
				);

				// Create new transaction from current ghost to target ghost
				(
					bytes32 generated_tx_id,
					address newActivator
				) = _addTransaction(
						recipient, // sender (current ghost)
						message.recipient, // recipient (target ghost)
						5, // or pass as part of message.data
						0, // or pass as part of message.data
						message.data // transaction data
					);
				emit InternalMessageProcessed(
					generated_tx_id,
					message.recipient,
					newActivator
				);
			}
		}
	}

	// SETTERS

	/**
	 * @notice Sets the ghost factory contract address
	 * @param _ghostFactory Address of the ghost factory contract
	 */
	function setGhostFactory(address _ghostFactory) external onlyOwner {
		ghostFactory = IGhostFactory(_ghostFactory);
		emit GhostFactorySet(_ghostFactory);
	}

	function setGenManager(address _genManager) external onlyOwner {
		genManager = IGenManager(_genManager);
		emit GenManagerSet(_genManager);
	}

	function setGenTransactions(address _genTransactions) external onlyOwner {
		genTransactions = ITransactions(_genTransactions);
		emit GenTransactionsSet(_genTransactions);
	}

	function setGenQueue(address _genQueue) external onlyOwner {
		genQueue = IQueues(_genQueue);
		emit GenQueueSet(_genQueue);
	}

	function setGenStaking(address _genStaking) external onlyOwner {
		genStaking = IGenStaking(_genStaking);
		emit GenStakingSet(_genStaking);
	}

	function setGenMessages(address _genMessages) external onlyOwner {
		genMessages = IMessages(_genMessages);
		emit GenMessagesSet(_genMessages);
	}

	function setAcceptanceTimeout(
		uint256 _acceptanceTimeout
	) external onlyOwner {
		ACCEPTANCE_TIMEOUT = _acceptanceTimeout;
	}

	function setActivationTimeout(
		uint256 _activationTimeout
	) external onlyOwner {
		ACTIVATION_TIMEOUT = _activationTimeout;
	}

	function setFeeManager(address _feeManager) external onlyOwner {
		feeManager = IFeeManager(_feeManager);
		emit FeeManagerSet(_feeManager);
	}

	// EVENTS
	event GhostFactorySet(address indexed ghostFactory);
	event GenManagerSet(address indexed genManager);
	event GenTransactionsSet(address indexed genTransactions);
	event GenQueueSet(address indexed genQueue);
	event GenStakingSet(address indexed genStaking);
	event GenMessagesSet(address indexed genMessages);
	event NewTransaction(
		bytes32 indexed tx_id,
		address indexed recipient,
		address indexed activator
	);
	event TransactionLeaderRotated(
		bytes32 indexed tx_id,
		address indexed newLeader
	);
	event TransactionActivated(
		bytes32 indexed tx_id,
		address indexed leader,
		address[] validators
	);
	event TransactionReceiptProposed(bytes32 indexed tx_id);
	event VoteCommitted(
		bytes32 indexed tx_id,
		address indexed validator,
		bool isLastVote
	);
	event VoteRevealed(
		bytes32 indexed tx_id,
		address indexed validator,
		ITransactions.VoteType voteType,
		bool isLastVote,
		ITransactions.ResultType result
	);
	event TransactionAccepted(bytes32 indexed tx_id);
	event TransactionLeaderTimeout(bytes32 indexed tx_id);
	event TransactionFinalized(bytes32 indexed tx_id);
	event TransactionNeedsRecomputation(bytes32[] tx_ids);
	event AppealStarted(
		bytes32 indexed tx_id,
		address indexed appealer,
		uint256 appealBond,
		address[] appealValidators
	);
	event InternalMessageProcessed(
		bytes32 indexed tx_id,
		address indexed recipient,
		address indexed activator
	);
	event TransactionCancelled(bytes32 indexed tx_id, address indexed sender);
	event FeeManagerSet(address indexed feeManager);
}