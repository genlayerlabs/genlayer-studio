// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { ITransactions } from "../transactions/interfaces/ITransactions.sol";
import { IIdleness } from "../transactions/interfaces/IIdleness.sol";
import { IGenManager } from "./IGenManager.sol";
import { IQueues } from "./IQueues.sol";
import { IGhostFactory } from "./IGhostFactory.sol";
import { IGenStaking } from "./IGenStaking.sol";
import { IMessages } from "./IMessages.sol";

interface IConsensusMain {
	struct AddTransactionParams {
		address sender;
		bytes data;
	}

	struct ExternalContracts {
		IGenManager genManager;
		ITransactions genTransactions;
		IQueues genQueue;
		IGhostFactory ghostFactory;
		IGenStaking genStaking;
		IMessages genMessages;
		IIdleness idleness;
	}

	/// @notice Returns the external contract addresses
	function contracts() external view returns (ExternalContracts memory);

	function txStatus(
		bytes32 _txId
	) external view returns (ITransactions.TransactionStatus);

	function genManager() external view returns (IGenManager);

	function txActivator(bytes32 _txId) external view returns (address);

	function getActivatorForTx(
		bytes32 _txId,
		uint256 _txSlot
	) external view returns (address);

	function txLeaderIndex(bytes32 _txId) external view returns (uint);

	function validatorsCountForTx(bytes32 _txId) external view returns (uint);

	function getValidatorsForTx(
		bytes32 _txId
	) external view returns (address[] memory);

	function voteCommittedCountForTx(
		bytes32 _txId
	) external view returns (uint);

	function voteRevealedCountForTx(bytes32 _txId) external view returns (uint);

	function validatorIsActiveForTx(
		bytes32 _txId,
		address _validator
	) external view returns (bool);

	function voteCommittedForTx(
		bytes32 _txId,
		address _validator
	) external view returns (bool);

	function addTransaction(bytes memory _transaction) external;

	function isCurrentActivator(
		uint256 proposingTimestamp,
		uint256 addedTimestamp,
		bytes32 randomSeed
	) external view returns (bool);

	function activateTransaction(
		bytes32 _txId,
		bytes calldata _vrfProof
	) external;

	function proposeReceipt(
		bytes memory _receipt,
		bytes calldata _vrfProof
	) external;

	function commitVote(bytes32 _txId, bytes32 _voteHash) external;

	function revealVote(bytes32 _txId, bytes32 _voteHash) external;

	function finalizeTransaction(bytes32 _txId) external;
}