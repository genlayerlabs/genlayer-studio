// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import "./interfaces/IQueues.sol";
import "./utils/Errors.sol";

contract Queues is
	Initializable,
	Ownable2StepUpgradeable,
	ReentrancyGuardUpgradeable,
	AccessControlUpgradeable
{
	struct QueueInfo {
		uint head;
		uint tail;
		mapping(uint => bytes32) slotToTxId;
		mapping(bytes32 => uint) txIdToSlot;
	}

	struct RecipientQueues {
		QueueInfo pending;
		QueueInfo accepted;
		QueueInfo undetermined;
		uint finalizedCount;
		uint issuedTxCount;
		mapping(bytes32 => uint) txIdToFinalizedSlot;
		mapping(bytes32 => IQueues.QueueType) txIdToQueueType;
	}

	address public genConsensus;
	mapping(address => RecipientQueues) private recipientQueues;
	mapping(bytes32 => IQueues.LastQueueModification)
		public lastQueueModification;

	event GenConsensusSet(address indexed genConsensus);
	event QueueOperationPerformed(
		address indexed recipient,
		bytes32 indexed txId,
		IQueues.QueueType queueType,
		uint slot
	);

	function initialize(address _genConsensus) public initializer {
		__Ownable_init(msg.sender);
		__Ownable2Step_init();
		__ReentrancyGuard_init();
		__AccessControl_init();
		genConsensus = _genConsensus;
	}

	function getTransactionQueueType(
		bytes32 txId
	) external view returns (IQueues.QueueType) {
		return recipientQueues[msg.sender].txIdToQueueType[txId];
	}

	function getTransactionQueuePosition(
		bytes32 txId
	) external view returns (uint) {
		return recipientQueues[msg.sender].pending.txIdToSlot[txId];
	}

	function getLastQueueModification(
		bytes32 txId
	) external view returns (IQueues.LastQueueModification memory) {
		return lastQueueModification[txId];
	}

	function isAtFinalizedQueueHead(
		address recipient,
		bytes32 txId
	) external view returns (bool) {
		RecipientQueues storage queues = recipientQueues[recipient];
		return queues.txIdToFinalizedSlot[txId] == queues.finalizedCount;
	}

	function getAcceptedCount(
		address recipient
	) external view returns (uint256) {
		return recipientQueues[recipient].accepted.tail;
	}

	function getAcceptedTxId(
		address recipient,
		uint256 slot
	) external view returns (bytes32) {
		return recipientQueues[recipient].accepted.slotToTxId[slot];
	}

	function getFinalizedCount(
		address recipient
	) external view returns (uint256) {
		return recipientQueues[recipient].finalizedCount;
	}

	function getFinalizedTxId(
		address recipient,
		uint256 slot
	) external view returns (bytes32) {
		return recipientQueues[recipient].accepted.slotToTxId[slot];
	}

	function addTransactionToPendingQueue(
		address recipient,
		bytes32 txId
	)
		external
		onlyConsensus
		returns (uint slot, bytes32[] memory txsForRecomputation)
	{
		RecipientQueues storage queues = recipientQueues[recipient];
		QueueInfo storage pendingQueue = queues.pending;

		if (queues.txIdToQueueType[txId] == IQueues.QueueType.None) {
			queues.txIdToQueueType[txId] = IQueues.QueueType.Pending;
			slot = pendingQueue.tail;
			pendingQueue.slotToTxId[slot] = txId;
			pendingQueue.txIdToSlot[txId] = slot;
			pendingQueue.tail++;

			queues.txIdToFinalizedSlot[txId] = queues.issuedTxCount++;

			emit QueueOperationPerformed(
				recipient,
				txId,
				IQueues.QueueType.Pending,
				slot
			);
		} else {
			// Get the slot of the transaction in pending queue
			slot = pendingQueue.txIdToSlot[txId];

			// Create array to store transactions that need recomputation
			txsForRecomputation = new bytes32[](pendingQueue.head - slot);
			uint txIndex = 0;

			// Collect all transactions after this slot until head for recomputation
			for (uint i = slot + 1; i < pendingQueue.head; i++) {
				bytes32 txToReset = pendingQueue.slotToTxId[i];
				txsForRecomputation[txIndex++] = txToReset;
				// Only reset to pending if it's not already pending
				if (
					queues.txIdToQueueType[txToReset] !=
					IQueues.QueueType.Pending
				) {
					queues.txIdToQueueType[txToReset] = IQueues
						.QueueType
						.Pending;
				}
			}

			// Reset the pending queue head to this slot
			pendingQueue.head = slot;
		}
	}

	function addTransactionToAcceptedQueue(
		address recipient,
		bytes32 txId
	) external onlyConsensus returns (uint slot) {
		RecipientQueues storage queues = recipientQueues[recipient];
		QueueInfo storage acceptedQueue = queues.accepted;

		queues.txIdToQueueType[txId] = IQueues.QueueType.Accepted;
		queues.pending.head++;
		_checkAndMovePendingHead(recipient);

		slot = acceptedQueue.tail;
		acceptedQueue.slotToTxId[slot] = txId;
		acceptedQueue.txIdToSlot[txId] = slot;
		acceptedQueue.tail++;

		lastQueueModification[txId] = IQueues.LastQueueModification({
			lastQueueType: IQueues.QueueType.Pending,
			lastQueueTimestamp: block.timestamp
		});

		emit QueueOperationPerformed(
			recipient,
			txId,
			IQueues.QueueType.Accepted,
			slot
		);
	}

	function addTransactionToUndeterminedQueue(
		address recipient,
		bytes32 txId
	) external onlyConsensus returns (uint slot) {
		// Remove from pending queue by setting slot to max uint
		recipientQueues[recipient].txIdToQueueType[txId] = IQueues
			.QueueType
			.Undetermined;
		recipientQueues[recipient].pending.head++;
		_checkAndMovePendingHead(recipient);
		slot = recipientQueues[recipient].undetermined.tail;
		recipientQueues[recipient].undetermined.slotToTxId[slot] = txId;
		recipientQueues[recipient].undetermined.txIdToSlot[txId] = slot;
		recipientQueues[recipient].undetermined.tail++;
		lastQueueModification[txId] = IQueues.LastQueueModification({
			lastQueueType: IQueues.QueueType.Pending,
			lastQueueTimestamp: block.timestamp
		});
	}

	function addTransactionToFinalizedQueue(
		address recipient,
		bytes32 txId
	) external onlyConsensus {
		if (
			recipientQueues[recipient].finalizedCount <
			recipientQueues[recipient].issuedTxCount
		) {
			recipientQueues[recipient].txIdToFinalizedSlot[
				txId
			] = recipientQueues[recipient].finalizedCount;
			++recipientQueues[recipient].finalizedCount;
		}
	}

	function setGenConsensus(address _genConsensus) external onlyOwner {
		genConsensus = _genConsensus;

		emit GenConsensusSet(_genConsensus);
	}

	modifier onlyConsensus() {
		if (msg.sender != genConsensus) {
			revert Errors.NotConsensus();
		}
		_;
	}

	function isAtPendingQueueHead(
		address recipient,
		bytes32 txId
	) external view returns (bool) {
		RecipientQueues storage queues = recipientQueues[recipient];
		return queues.pending.txIdToSlot[txId] == queues.pending.head;
	}

	/**
	 * @notice Removes a transaction from the pending queue
	 * @param recipient The address of the recipient
	 * @param txId The transaction ID to remove
	 * @dev Only callable by consensus contract
	 * @dev Transaction must be at the head of the pending queue
	 */
	function removeTransactionFromPendingQueue(
		address recipient,
		bytes32 txId
	) external onlyConsensus {
		RecipientQueues storage queues = recipientQueues[recipient];
		QueueInfo storage pendingQueue = queues.pending;

		// Verify transaction is at the head of pending queue
		if (pendingQueue.txIdToSlot[txId] != pendingQueue.head) {
			revert Errors.TransactionNotAtPendingQueueHead();
		}

		// Remove transaction from queue mappings
		delete pendingQueue.slotToTxId[pendingQueue.head];
		delete pendingQueue.txIdToSlot[txId];
		delete queues.txIdToQueueType[txId];

		// Increment head to move to next transaction
		pendingQueue.head++;
		_checkAndMovePendingHead(recipient);
		// Update last queue modification
		lastQueueModification[txId] = IQueues.LastQueueModification({
			lastQueueType: IQueues.QueueType.None,
			lastQueueTimestamp: block.timestamp
		});

		if (
			recipientQueues[recipient].finalizedCount <
			recipientQueues[recipient].issuedTxCount
		) {
			++recipientQueues[recipient].finalizedCount;
		}

		emit QueueOperationPerformed(
			recipient,
			txId,
			IQueues.QueueType.None,
			type(uint).max
		);
	}

	function _checkAndMovePendingHead(address recipient) internal {
		bytes32 nextTxId = recipientQueues[recipient].pending.slotToTxId[
			recipientQueues[recipient].pending.head
		];
		while (
			nextTxId != bytes32(0) &&
			recipientQueues[recipient].txIdToQueueType[nextTxId] ==
			IQueues.QueueType.None
		) {
			recipientQueues[recipient].pending.head++;
			nextTxId = recipientQueues[recipient].pending.slotToTxId[
				recipientQueues[recipient].pending.head
			];
		}
	}
}