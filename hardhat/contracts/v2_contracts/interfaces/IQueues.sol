// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { ITransactions } from "../transactions/interfaces/ITransactions.sol";

interface IQueues {
	enum QueueType {
		None,
		Pending,
		Accepted,
		Undetermined
	}

	struct LastQueueModification {
		QueueType lastQueueType;
		uint256 lastQueueTimestamp;
	}

	function getTransactionQueueType(
		bytes32 txId
	) external view returns (QueueType);

	function getTransactionQueuePosition(
		bytes32 txId
	) external view returns (uint);

	function getLastQueueModification(
		bytes32 txId
	) external view returns (LastQueueModification memory);

	function addTransactionToPendingQueue(
		address recipient,
		bytes32 txId
	) external returns (uint256, bytes32[] memory);

	function addTransactionToFinalizedQueue(
		address recipient,
		bytes32 txId
	) external;

	function isAtFinalizedQueueHead(
		address recipient,
		bytes32 txId
	) external view returns (bool);

	function isAtPendingQueueHead(
		address recipient,
		bytes32 txId
	) external view returns (bool);

	function addTransactionToAcceptedQueue(
		address recipient,
		bytes32 txId
	) external returns (uint slot);

	function addTransactionToUndeterminedQueue(
		address recipient,
		bytes32 txId
	) external returns (uint slot);

	function removeTransactionFromPendingQueue(
		address recipient,
		bytes32 txId
	) external;

	function getAcceptedCount(
		address recipient
	) external view returns (uint256);
	function getAcceptedTxId(
		address recipient,
		uint256 slot
	) external view returns (bytes32);

	function getFinalizedCount(
		address recipient
	) external view returns (uint256);
	function getFinalizedTxId(
		address recipient,
		uint256 slot
	) external view returns (bytes32);
}