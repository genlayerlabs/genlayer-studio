// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { ITransactions } from "./interfaces/ITransactions.sol";
import { IGenStaking } from "../interfaces/IGenStaking.sol";
import { IFeeManager } from "../interfaces/IFeeManager.sol";
import { Utils } from "./Utils.sol";
import { ArrayUtils } from "../utils/ArrayUtils.sol";
import { RandomnessUtils } from "../utils/RandomnessUtils.sol";
import { Errors } from "../utils/Errors.sol";

contract Rounds {
	/// @notice The owner of the contract
	address public owner;

	/// @notice The fee manager contract
	IFeeManager public feeManager;

	/// @notice The staking contract
	IGenStaking public staking;

	/// @notice The utils contract
	Utils public utils;

	constructor(address _staking, address _feeManager, address _utils) {
		owner = msg.sender;
		staking = IGenStaking(_staking);
		feeManager = IFeeManager(_feeManager);
		utils = Utils(_utils);
	}

	modifier onlyOwner() {
		if (msg.sender != owner) {
			revert Errors.CallerNotOwner();
		}
		_;
	}

	function createNewRound(
		ITransactions.Transaction memory _tx,
		uint256 _round,
		uint256 _appealBond,
		bytes32 _randomSeed,
		uint256 _rotationsLeft,
		bool _isRotation
	)
		external
		view
		returns (
			address[] memory roundValidators,
			uint256 leaderIndex,
			ITransactions.UpdateTransactionInfo memory info
		)
	{
		return
			_createNewRound(
				_tx,
				_round,
				_appealBond,
				_randomSeed,
				_rotationsLeft,
				_isRotation
			);
	}

	function rotateLeader(
		ITransactions.Transaction memory _tx
	)
		external
		view
		returns (
			address newLeader,
			ITransactions.UpdateTransactionInfo memory info
		)
	{
		uint256 round = _tx.roundData.length - 1;
		uint256 rotationsLeft = _tx.roundData[round].rotationsLeft;
		if (rotationsLeft == 0) {
			revert Errors.NoRotationsLeft();
		}
		address[] memory newValidators;
		uint256 leaderIndex;
		(newValidators, leaderIndex, info) = _createNewRound(
			_tx,
			round,
			0,
			_tx.randomSeed,
			rotationsLeft - 1,
			true
		);
		newLeader = newValidators[leaderIndex];
	}

	function isEmptyRoundData(
		ITransactions.RoundData memory _roundData
	) external pure returns (bool) {
		return
			_roundData.roundValidators.length == 0 &&
			_roundData.validatorVotes.length == 0 &&
			_roundData.validatorVotesHash.length == 0 &&
			_roundData.leaderIndex == 0 &&
			_roundData.votesCommitted == 0 &&
			_roundData.votesRevealed == 0 &&
			_roundData.rotationsLeft == 0 &&
			_roundData.result == ITransactions.ResultType(0) &&
			_roundData.appealBond == 0;
	}

	function createAnEmptyRound()
		external
		pure
		returns (ITransactions.RoundData memory)
	{
		return
			ITransactions.RoundData(
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
	}

	function _createNewRound(
		ITransactions.Transaction memory _tx,
		uint256 _round,
		uint256 _appealBond,
		bytes32 _randomSeed,
		uint256 _rotationsLeft,
		bool _isRotation
	)
		internal
		view
		returns (
			address[] memory roundValidators,
			uint256 leaderIndex,
			ITransactions.UpdateTransactionInfo memory info
		)
	{
		address[] memory txConsumedValidators = _tx.consumedValidators;

		if (_isRotation) {
			address[] memory txRoundValidators = _tx
				.roundData[_round]
				.roundValidators;
			ITransactions.RoundData memory txRoundData = _tx.roundData[_round];

			(roundValidators, leaderIndex) = _getValidatorsAndLeaderIndex(
				_randomSeed,
				1,
				txConsumedValidators
			);
			txConsumedValidators = ArrayUtils.concatArraysAndDropIndex(
				txConsumedValidators,
				roundValidators,
				txConsumedValidators.length
			);
			roundValidators = ArrayUtils.concatArraysAndDropIndex(
				txRoundValidators,
				roundValidators,
				txRoundData.leaderIndex
			);
			leaderIndex = RandomnessUtils.randomlySelectIndex(
				uint256(_randomSeed),
				txConsumedValidators.length,
				leaderIndex,
				roundValidators.length
			);
			txRoundData = ITransactions.RoundData(
				_round,
				leaderIndex,
				0,
				0,
				_appealBond,
				_rotationsLeft,
				ITransactions.ResultType(0),
				roundValidators,
				new bytes32[](roundValidators.length),
				new ITransactions.VoteType[](roundValidators.length)
			);
			// Create the update info
			info = utils.createDefaultUpdateInfo();
			info.id = _tx.id;
			info.consumedValidators = txConsumedValidators;
			info.roundData = txRoundData;
			info.round = _round;
		} else {
			if (_round % 2 == 0 && _round > 0) {
				address[] memory missingValidators;
				(
					roundValidators,
					missingValidators
				) = _getPreviousRoundValidators(_tx, _round, _randomSeed);
				txConsumedValidators = ArrayUtils.concatArraysAndDropIndex(
					txConsumedValidators,
					missingValidators,
					txConsumedValidators.length
				);
				leaderIndex = RandomnessUtils.randomlySelectIndex(
					uint256(_randomSeed),
					txConsumedValidators.length,
					leaderIndex,
					roundValidators.length
				);
			} else {
				(roundValidators, leaderIndex) = _getValidatorsAndLeaderIndex(
					_randomSeed,
					feeManager.getValidatorsPerRound(_round),
					txConsumedValidators
				);
				txConsumedValidators = ArrayUtils.concatArraysAndDropIndex(
					txConsumedValidators,
					roundValidators,
					txConsumedValidators.length
				);
			}
			ITransactions.RoundData memory newRoundData = ITransactions
				.RoundData(
					_round,
					leaderIndex,
					0,
					0,
					_appealBond,
					_rotationsLeft,
					ITransactions.ResultType(0),
					roundValidators,
					new bytes32[](roundValidators.length),
					new ITransactions.VoteType[](roundValidators.length)
				);
			// Create the update info
			info = utils.createDefaultUpdateInfo();
			info.id = _tx.id;
			info.consumedValidators = txConsumedValidators;
			info.roundData = newRoundData;
			info.round = _round;
		}
	}

	function _getPreviousRoundValidators(
		ITransactions.Transaction memory _tx,
		uint256 _round,
		bytes32 _randomSeed
	)
		internal
		view
		returns (
			address[] memory previousRoundValidators,
			address[] memory missingValidators
		)
	{
		uint256 previousRoundValidatorsLength = _tx
			.roundData[_round - 2]
			.roundValidators
			.length;
		if (previousRoundValidatorsLength > 0) {
			previousRoundValidators = ArrayUtils.concatArraysAndDropIndex(
				_tx.roundData[_round - 2].roundValidators,
				_tx.roundData[_round - 1].roundValidators,
				_tx.roundData[_round - 2].leaderIndex
			);
		} else {
			uint256 validPreviousRound = 0;
			for (uint256 i = _round - 2; i > 0; i -= 2) {
				if (_tx.roundData[i].roundValidators.length > 0) {
					validPreviousRound = i;
					break;
				}
			}
			uint256 missingValidatorsLength = feeManager.getValidatorsPerRound(
				_round
			) -
				_tx.roundData[validPreviousRound].roundValidators.length -
				_tx.roundData[_round - 1].roundValidators.length;
			(missingValidators, ) = _getValidatorsAndLeaderIndex(
				_randomSeed,
				missingValidatorsLength,
				_tx.consumedValidators
			);
			previousRoundValidators = ArrayUtils.concatArraysAndDropIndex(
				_tx.roundData[validPreviousRound].roundValidators,
				_tx.roundData[_round - 1].roundValidators,
				_tx.roundData[validPreviousRound].leaderIndex
			);
			previousRoundValidators = ArrayUtils.concatArraysAndDropIndex(
				previousRoundValidators,
				missingValidators,
				previousRoundValidators.length
			);
		}
	}

	function _getValidatorsAndLeaderIndex(
		bytes32 _randomSeed,
		uint256 numValidators,
		address[] memory consumedValidators
	) internal view returns (address[] memory validators, uint256 leaderIndex) {
		(validators, leaderIndex) = staking.getValidatorsForTx(
			_randomSeed,
			numValidators,
			consumedValidators
		);
	}

	function setContracts(
		address _staking,
		address _feeManager,
		address _utils
	) external onlyOwner {
		staking = IGenStaking(_staking);
		feeManager = IFeeManager(_feeManager);
		utils = Utils(_utils);
	}
}