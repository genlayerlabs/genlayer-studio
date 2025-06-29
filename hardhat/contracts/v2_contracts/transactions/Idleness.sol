// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { IIdleness } from "./interfaces/IIdleness.sol";
import { ITransactions } from "./interfaces/ITransactions.sol";
import { IGenStaking } from "../interfaces/IGenStaking.sol";
import { ArrayUtils } from "../utils/ArrayUtils.sol";
import { Errors } from "../utils/Errors.sol";
import { Utils } from "./Utils.sol";

contract Idleness is IIdleness {
	uint256 public constant BAN_MIN = 1; // in epochs
	uint256 public constant BAN_MAX = 10;
	uint256 public constant SLASH_MIN = 1; // in basis points (e.g. 1 = 0.01%)
	uint256 public constant SLASH_MAX = 10000;

	/// @notice The owner of the contract
	address public owner;

	/// @notice Ban period in epochs
	uint256 public banPeriod;

	/// @notice Slash percentage in basis points
	uint256 public slashPercentage;

	/// @notice Number of result to return per page
	uint256 public pageSize;

	/// @notice Consolidated external contract addresses used in Idleness
	ExternalContracts public contracts;

	/// @notice Timeouts for the transaction
	Timeouts public timeouts;

	constructor(address _transactions, address _staking, address _utils) {
		owner = msg.sender;
		contracts.transactions = ITransactions(_transactions);
		contracts.staking = IGenStaking(_staking);
		contracts.utils = Utils(_utils);
	}

	modifier onlyOwner() {
		if (msg.sender != owner) {
			revert Errors.CallerNotOwner();
		}
		_;
	}

	/// External functions

	function checkIdle(
		ITransactions.Transaction memory _tx
	) external returns (ITransactions.UpdateTransactionInfo memory) {
		ITransactions.TransactionStatus status = _tx.status;
		if (status == ITransactions.TransactionStatus.Uninitialized) {
			revert Errors.TransactionNotFound();
		}
		uint256 slots = _getSlotsElapsed(
			status,
			_tx.timestamps,
			block.timestamp
		);
		if (slots > 0) {
			return _replaceIdle(_tx, status, slots);
		}
		return contracts.utils.createDefaultUpdateInfo();
	}

	/// @notice Gets the next validators for a given random seed and number of slots
	/// @param _randomSeed The random seed
	/// @param _slots The number of slots
	/// @param _consumedValidators The consumed validators
	/// @return The next validators
	function getNextValidators(
		bytes32 _randomSeed,
		uint256 _slots,
		address[] memory _consumedValidators,
		bool _isWeighted
	) external view returns (address[] memory) {
		return
			_getNextValidators(
				_randomSeed,
				_slots,
				_consumedValidators,
				_isWeighted
			);
	}

	/// @notice Gets the defined timeouts
	/// @return The timeouts
	function getTimeouts() external view returns (IIdleness.Timeouts memory) {
		return timeouts;
	}

	/// @notice Gets the page size
	/// @return The page size
	function getPageSize() external view returns (uint256) {
		return pageSize;
	}

	/// Internal functions

	/// @dev Gets the number of timeout slots elapsed since the transaction was created
	function _getSlotsElapsed(
		ITransactions.TransactionStatus _status,
		ITransactions.Timestamps memory _timestamps,
		uint256 _currentTimestamp
	) internal view returns (uint256) {
		if (
			_status == ITransactions.TransactionStatus.Pending &&
			_currentTimestamp > _timestamps.pending &&
			timeouts.activate > 0
		) {
			return
				(_currentTimestamp - _timestamps.pending) / timeouts.activate;
		} else if (
			_status == ITransactions.TransactionStatus.Proposing &&
			_currentTimestamp > _timestamps.activated &&
			timeouts.propose > 0
		) {
			return
				(_currentTimestamp - _timestamps.activated) / timeouts.propose;
		} else if (
			(_status == ITransactions.TransactionStatus.Committing ||
				_status == ITransactions.TransactionStatus.AppealCommitting) &&
			_currentTimestamp > _timestamps.proposed &&
			timeouts.commit > 0
		) {
			return (_currentTimestamp - _timestamps.proposed) / timeouts.commit;
		} else if (
			(_status == ITransactions.TransactionStatus.Revealing ||
				_status == ITransactions.TransactionStatus.AppealRevealing) &&
			_currentTimestamp > _timestamps.committed &&
			timeouts.reveal > 0
		) {
			return
				(_currentTimestamp - _timestamps.committed) / timeouts.reveal;
		}
		return 0;
	}

	function _replaceIdle(
		ITransactions.Transaction memory _tx,
		ITransactions.TransactionStatus _status,
		uint256 _slots
	) internal returns (ITransactions.UpdateTransactionInfo memory info) {
		info = contracts.utils.createDefaultUpdateInfo();
		address[] memory validators;
		uint256 total;

		if (_status == ITransactions.TransactionStatus.Pending) {
			// Replace activator
			validators = _getNextValidators(
				_tx.randomSeed,
				_slots + 1,
				new address[](0),
				false
			);
			total = _slots;

			// Create the update info
			info.id = _tx.id;
			info.activator = validators[_slots];
			info.timestamps.pending = block.timestamp;

			emit IIdleness.TransactionActivatorChanged(
				_tx.id,
				validators[_slots]
			);
		} else if (_status == ITransactions.TransactionStatus.Proposing) {
			// Replace leader
			validators = _getNextValidators(
				_tx.randomSeed,
				_slots + 1,
				_tx.consumedValidators,
				true
			);
			total = _slots;

			uint256 round = _tx.roundData.length - 1;
			ITransactions.RoundData memory roundData = _tx.roundData[round];

			// Get the current leader
			uint256 currentLeaderIndex = roundData.leaderIndex;
			address currentLeader = roundData.roundValidators[
				currentLeaderIndex
			];

			// Update the last leader on the idle validators list because it could be rotated out
			validators[_slots - 1] = currentLeader;

			// Update the round validators with the new leader
			roundData.roundValidators[currentLeaderIndex] = validators[_slots];

			// Update the consumed validators
			address[] memory newLeader = new address[](1);
			newLeader[0] = validators[_slots];
			address[] memory txConsumedValidators = _tx.consumedValidators;
			txConsumedValidators = ArrayUtils.concatArraysAndDropIndex(
				txConsumedValidators,
				newLeader,
				txConsumedValidators.length
			);

			// Create the update info
			info.id = _tx.id;
			info.timestamps.activated = block.timestamp;
			info.consumedValidators = txConsumedValidators;
			info.roundData = roundData;
			info.round = round;

			emit IIdleness.TransactionLeaderChanged(_tx.id, validators[_slots]);
		} else if (
			_status == ITransactions.TransactionStatus.Committing ||
			_status == ITransactions.TransactionStatus.AppealCommitting
		) {
			// Replace idle validators
			(total, validators) = _getIdleValidators(_tx);

			// Get the new validators
			address[] memory newValidators = _getNextValidators(
				_tx.randomSeed,
				total,
				_tx.consumedValidators,
				true
			);

			// Update the round validators with the new validators
			uint256 round = _tx.roundData.length - 1;
			ITransactions.RoundData memory roundData = _tx.roundData[round];
			for (uint256 i = 0; i < total; i++) {
				// Get the index of the old validator
				(uint256 oldValidatorIndex, ) = ArrayUtils.getIndex(
					roundData.roundValidators,
					validators[i]
				);
				// Replace the old validator with the new validator
				roundData.roundValidators[oldValidatorIndex] = newValidators[i];
			}

			// Update the consumed validators
			address[] memory txConsumedValidators = _tx.consumedValidators;
			txConsumedValidators = ArrayUtils.concatArraysAndDropIndex(
				txConsumedValidators,
				validators,
				txConsumedValidators.length
			);

			// Create the update info
			info.id = _tx.id;
			info.timestamps.proposed = block.timestamp;
			info.consumedValidators = txConsumedValidators;
			info.roundData = roundData;
			info.round = round;

			emit IIdleness.TransactionValidatorsChanged(_tx.id, newValidators);
		} else if (
			_status == ITransactions.TransactionStatus.Revealing ||
			_status == ITransactions.TransactionStatus.AppealRevealing
		) {
			(total, validators) = _getIdleValidators(_tx);
		}

		// slash the idle validators
		if (total == 0) {
			revert Errors.NoIdleValidator();
		}
		for (uint256 i = 0; i < total; ++i) {
			if (validators[i] != address(0)) {
				_slash(_tx.id, validators[i]);
			}
		}
	}

	function _getIdleValidators(
		ITransactions.Transaction memory _tx
	)
		internal
		pure
		returns (uint256 idleValidatorsCount, address[] memory idleValidators)
	{
		uint256 round = _tx.roundData.length - 1;
		ITransactions.RoundData memory roundData = _tx.roundData[round];
		address[] memory roundValidators = roundData.roundValidators;
		uint256 length = roundValidators.length;
		ITransactions.TransactionStatus status = _tx.status;

		idleValidators = new address[](length);

		// check if there are validators that have not committed yet
		if (
			status == ITransactions.TransactionStatus.Committing ||
			status == ITransactions.TransactionStatus.AppealCommitting
		) {
			uint256 votesCommitted = _tx.roundData[round].votesCommitted;
			if (votesCommitted == roundValidators.length) {
				return (0, new address[](0));
			}
			for (uint256 i = 0; i < length; i++) {
				address roundValidator = roundValidators[i];
				(uint256 validatorIndex, ) = ArrayUtils.getIndex(
					_tx.roundData[round].roundValidators,
					roundValidator
				);
				if (
					_tx.roundData[round].validatorVotesHash[validatorIndex] ==
					bytes32(0)
				) {
					idleValidators[idleValidatorsCount++] = roundValidator;
				}
			}
		}
		// check if there are validators that have not revealed yet
		else if (
			status == ITransactions.TransactionStatus.Revealing ||
			status == ITransactions.TransactionStatus.AppealRevealing
		) {
			uint256 votesRevealed = _tx.roundData[round].votesRevealed;
			if (votesRevealed == roundValidators.length) {
				return (0, new address[](0));
			}
			for (uint256 i = 0; i < length; i++) {
				address roundValidator = roundValidators[i];
				(uint256 validatorIndex, ) = ArrayUtils.getIndex(
					_tx.roundData[round].roundValidators,
					roundValidator
				);
				if (
					_tx.roundData[round].validatorVotes[validatorIndex] ==
					ITransactions.VoteType(0)
				) {
					idleValidators[idleValidatorsCount++] = roundValidator;
				}
			}
		} else {
			return (0, new address[](0));
		}
		return (idleValidatorsCount, idleValidators);
	}

	function _slash(bytes32 _txId, address _validator) internal {
		contracts.staking.ban(_validator, banPeriod);
		contracts.staking.slash(_validator, slashPercentage);
		emit IIdleness.ValidatorSlashed(_txId, _validator);
	}

	function _getNextValidators(
		bytes32 _randomSeed,
		uint256 _slots,
		address[] memory _consumedValidators,
		bool _isWeighted
	) internal view returns (address[] memory validators) {
		return
			contracts.staking.getNextValidators(
				_randomSeed,
				_slots,
				_consumedValidators,
				_isWeighted
			);
	}

	/// Setters

	function setTimeouts(
		uint256 _activate,
		uint256 _propose,
		uint256 _commit,
		uint256 _reveal,
		uint256 _accept
	) external onlyOwner {
		timeouts = IIdleness.Timeouts({
			activate: _activate,
			propose: _propose,
			commit: _commit,
			reveal: _reveal,
			accept: _accept
		});
		emit IIdleness.TimeoutsSet(
			_activate,
			_propose,
			_commit,
			_reveal,
			_accept
		);
	}

	function setContracts(
		ITransactions _transactions,
		IGenStaking _staking,
		Utils _utils
	) external onlyOwner {
		contracts = IIdleness.ExternalContracts({
			transactions: _transactions,
			staking: _staking,
			utils: _utils
		});
	}

	function setBanPeriod(uint256 _banPeriod) external onlyOwner {
		if (_banPeriod < BAN_MIN || _banPeriod > BAN_MAX) {
			revert Errors.InvalidBanPeriod();
		}
		banPeriod = _banPeriod;
	}

	function setSlashPercentage(uint256 _slashPercentage) external onlyOwner {
		if (_slashPercentage < SLASH_MIN || _slashPercentage > SLASH_MAX) {
			revert Errors.InvalidSlashPercentage();
		}
		slashPercentage = _slashPercentage;
	}

	function setPageSize(uint256 _pageSize) external onlyOwner {
		if (_pageSize < 1 || _pageSize > 100) {
			revert Errors.InvalidPageSize();
		}
		pageSize = _pageSize;
	}
}