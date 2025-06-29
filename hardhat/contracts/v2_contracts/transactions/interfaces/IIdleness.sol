// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import { ITransactions } from "./ITransactions.sol";
import { IGenStaking } from "../../interfaces/IGenStaking.sol";
import { Utils } from "../Utils.sol";

interface IIdleness {
	struct ExternalContracts {
		ITransactions transactions;
		IGenStaking staking;
		Utils utils;
	}

	struct Timeouts {
		uint256 activate;
		uint256 propose;
		uint256 commit;
		uint256 reveal;
		uint256 accept;
	}

	event ValidatorSlashed(bytes32 indexed txId, address indexed validator);
	event TransactionActivatorChanged(
		bytes32 indexed txId,
		address indexed newActivator
	);
	event TransactionLeaderChanged(
		bytes32 indexed txId,
		address indexed newLeader
	);
	event TransactionValidatorsChanged(
		bytes32 indexed txId,
		address[] indexed newValidators
	);
	event TimeoutsSet(
		uint256 activate,
		uint256 propose,
		uint256 commit,
		uint256 reveal,
		uint256 accept
	);

	function checkIdle(
		ITransactions.Transaction memory _transaction
	) external returns (ITransactions.UpdateTransactionInfo memory);

	function getNextValidators(
		bytes32 _randomSeed,
		uint256 _slots,
		address[] memory _consumedValidators,
		bool _isWeighted
	) external view returns (address[] memory);

	function getTimeouts() external view returns (Timeouts memory);

	function getPageSize() external view returns (uint256);
}