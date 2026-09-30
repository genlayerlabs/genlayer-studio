// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
import "./interfaces/IFeeManager.sol";
import "./utils/Errors.sol";

contract FeeManager is
	Initializable,
	Ownable2StepUpgradeable,
	ReentrancyGuardUpgradeable,
	PausableUpgradeable
{
	// Rounds of validators per round
	// normal rounds are VALIDATORS_PER_ROUND[2n+1], n = 0, 1, 2, 3, ...
	// appeal rounds are VALIDATORS_PER_ROUND[2n], n = 1, 2, 3, ...
	uint[] public VALIDATORS_PER_ROUND = [
		5, // 0
		7, // 1
		11, // 2
		13, // 3
		23, // 4
		25, // 5
		47, // 6
		49, // 7
		95, // 8
		97, // 9
		191, // 10
		193, // 11
		383, // 12
		385, // 13
		767, // 14
		769, // 15
		1535, // 16
		1537 // 17
	];

	address public consensusMain;
	// Fee tracking
	mapping(bytes32 txId => IFeeManager.FeesDistribution)
		public feesDistributionForTx;
	mapping(bytes32 txId => mapping(uint256 round => mapping(uint256 rotation => IFeeManager.RotationRoundFees)))
		public rotationRoundFees;
	mapping(bytes32 txId => mapping(uint256 round => uint256))
		public rotationsUsedForTxRound;
	mapping(bytes32 txId => uint256 round) public currentRoundForTx;
	mapping(bytes32 txId => uint256) public transactionFeeBalance; // Balance per transaction
	mapping(bytes32 txId => mapping(address validator => uint256 finalFees))
		public distributedFeesPerValidatorForTx; // Final fees per transaction
	mapping(uint256 index => address validatorAddress)
		public validatorsIndexToAddress;
	mapping(bytes32 txId => uint256 numOfValidators)
		public numOfValidatorsForDistribution;
	mapping(bytes32 txId => address topUpAddress)
		public lastTransactionTopUpSender;
	mapping(bytes32 txId => uint256 feesAmount) public feesToReturnToUser;
	mapping(bytes32 txId => mapping(uint256 round => IFeeManager.AppealerRewards rewards))
		public feesToReturnToAppealer;

	modifier onlyConsensus() {
		if (msg.sender != consensusMain) {
			revert Errors.OnlyConsensusCanCall();
		}
		_;
	}

	function initialize(address _consensusMain) public initializer {
		__Ownable2Step_init();
		__Ownable_init(msg.sender);
		consensusMain = _consensusMain;
	}

	function getValidatorsPerRound(
		uint256 round
	) external view returns (uint256) {
		return VALIDATORS_PER_ROUND[round];
	}

	/**
	 * @notice Calculate fees needed for a new validation round
	 * @param txId The transaction ID
	 * @param _feesDistribution The fees distribution structure
	 * @param round The round number
	 * @return totalFeesToPay The required fees for the round
	 */
	function calculateRoundFees(
		bytes32 txId,
		IFeeManager.FeesDistribution memory _feesDistribution,
		uint256 _numOfValidators,
		uint256 round
	) external view returns (uint256 totalFeesToPay) {
		totalFeesToPay = _calculateRoundFees(
			txId,
			_feesDistribution,
			_numOfValidators,
			round
		);
	}

	/**
	 * @notice Top up fees for a transaction
	 * @param _txId The transaction ID
	 * @param _feesDistribution The fees distribution structure
	 * @param _amount The amount of fees to top up
	 * @param _performFeeChecks Whether to perform fee checks
	 */
	function topUpFees(
		bytes32 _txId,
		IFeeManager.FeesDistribution memory _feesDistribution,
		uint256 _amount,
		bool _performFeeChecks,
		address _sender
	) external onlyConsensus {
		_topUpFees(
			_txId,
			_feesDistribution,
			_amount,
			_performFeeChecks,
			_sender
		);
	}

	/// @notice Records a proposed receipt for a transaction round
	/// @dev Used to track leader proposals and timeouts for fee distribution
	/// @param _txId The unique identifier of the transaction
	/// @param _round The current validation round number
	/// @param _leader The address of the leader who proposed the receipt
	/// @param _leaderTimeout Whether the leader timed out during the proposal
	function recordProposedReceipt(
		bytes32 _txId,
		uint256 _round,
		address _leader,
		bool _leaderTimeout
	) external onlyConsensus {
		currentRoundForTx[_txId] = _round;
		IFeeManager.RoundTypes roundType = _leaderTimeout
			? IFeeManager.RoundTypes.LEADER_TIMEOUT_50_PERCENT
			: IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND;
		ITransactions.ResultType result = _leaderTimeout
			? ITransactions.ResultType.Timeout
			: ITransactions.ResultType.Idle;
		rotationRoundFees[_txId][_round][
			rotationsUsedForTxRound[_txId][_round]
		] = IFeeManager.RotationRoundFees({
			roundType: roundType,
			result: result,
			leader: _leader,
			appealBond: 0,
			validators: new address[](0),
			validatorsVotes: new ITransactions.VoteType[](0)
		});
		if (_round > 0) {
			_updateRoundTypesBasedOnCurrentRoundResult(
				_txId,
				_round,
				rotationsUsedForTxRound[_txId][_round],
				result,
				roundType
			);
		}
		rotationsUsedForTxRound[_txId][_round]++;
	}

	function recordRevealedVote(
		bytes32 _txId,
		uint256 _round,
		address _validator,
		bool _isLastVote,
		ITransactions.VoteType _voteType,
		ITransactions.ResultType _result
	) external onlyConsensus returns (bool feesForNewRoundApproved) {
		uint256 rotation = rotationsUsedForTxRound[_txId][_round] > 1
			? rotationsUsedForTxRound[_txId][_round] - 1
			: 0;
		rotationRoundFees[_txId][_round][rotation].validatorsVotes.push(
			_voteType
		);
		rotationRoundFees[_txId][_round][rotation].validators.push(_validator);
		if (_isLastVote) {
			rotationRoundFees[_txId][_round][rotation].result = _result;
			feesForNewRoundApproved = _checkFeesForNewRound(_txId, _round);
			_updateRoundTypesBasedOnCurrentRoundResult(
				_txId,
				_round,
				rotation,
				_result,
				IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND
			);
		}
	}

	function addAppealRound(
		bytes32 _txId,
		uint256 _round,
		uint256 _appealBond,
		address _appealer
	) external onlyConsensus returns (bool feesForNewRoundApproved) {
		uint256 currentRound = currentRoundForTx[_txId];
		if (_round <= currentRound) {
			revert Errors.AppealRoundAlreadyExists();
		}
		feesForNewRoundApproved = _checkFeesForNewRound(_txId, _round);
		if (!feesForNewRoundApproved) {
			revert Errors.AppealRoundNotPermitted();
		}
		currentRoundForTx[_txId] = _round;
		IFeeManager.RoundTypes roundType;
		IFeeManager.RotationRoundFees memory lastRoundFees = rotationRoundFees[
			_txId
		][_round][_round - 1];
		if (
			lastRoundFees.roundType ==
			IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND &&
			lastRoundFees.result == ITransactions.ResultType.MajorityDisagree
		) {
			roundType = IFeeManager.RoundTypes.APPEAL_LEADER_UNSUCCESSFUL;
		} else if (
			lastRoundFees.roundType ==
			IFeeManager.RoundTypes.LEADER_TIMEOUT_50_PERCENT &&
			lastRoundFees.result == ITransactions.ResultType.Timeout
		) {
			roundType = IFeeManager
				.RoundTypes
				.APPEAL_LEADER_TIMEOUT_UNSUCCESSFUL;
		} else {
			roundType = IFeeManager.RoundTypes.APPEAL_VALIDATOR_UNSUCCESSFUL;
		}
		rotationRoundFees[_txId][_round][1] = IFeeManager.RotationRoundFees({
			roundType: roundType,
			result: ITransactions.ResultType.Idle,
			leader: _appealer,
			appealBond: _appealBond,
			validators: new address[](0),
			validatorsVotes: new ITransactions.VoteType[](0)
		});
	}

	/**
	 * @notice Commit final fees for a transaction
	 * @param txId The transaction ID
	 */
	function commitFinalFees(bytes32 txId) external onlyConsensus {
		uint256 finalRound = currentRoundForTx[txId];
		uint256 validatorTimeoutFees = feesDistributionForTx[txId]
			.validatorsTimeout;
		uint256 leaderTimeoutFees = feesDistributionForTx[txId].leaderTimeout;
		uint256 amountDistributed;
		bool performRotationCheck;
		for (uint256 i = 0; i < finalRound; i++) {
			uint256 rotation = rotationsUsedForTxRound[txId][i] > 1
				? rotationsUsedForTxRound[txId][i] - 1
				: 0;
			IFeeManager.RotationRoundFees
				memory currentRoundFees = rotationRoundFees[txId][i][rotation];
			// TODO: finalize the round checks and attributions
			if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND
			) {
				_distributeFeesToValidators(
					txId,
					amountDistributed,
					0,
					validatorTimeoutFees,
					false,
					false,
					currentRoundFees
				);
				_distributeFeesToLeader(
					txId,
					amountDistributed,
					leaderTimeoutFees,
					currentRoundFees.leader
				);
				performRotationCheck = true;
			} else if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.LEADER_TIMEOUT_50_PERCENT
			) {
				_distributeFeesToLeader(
					txId,
					amountDistributed,
					leaderTimeoutFees / 2,
					currentRoundFees.leader
				);
			} else if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.SKIP_REWARDS_ROUND
			) {} else if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.SPLIT_PREV_APPEAL_BOND_ROUND
			) {
				uint256 previousAppealBond = rotationRoundFees[txId][i - 1][0]
					.appealBond;
				_distributeFeesToValidators(
					txId,
					amountDistributed,
					previousAppealBond,
					validatorTimeoutFees,
					false,
					false,
					currentRoundFees
				);
				_distributeFeesToLeader(
					txId,
					amountDistributed,
					leaderTimeoutFees,
					currentRoundFees.leader
				);
				feesToReturnToUser[txId] += previousAppealBond;
			} else if (
				currentRoundFees.roundType ==
				IFeeManager
					.RoundTypes
					.LEADER_TIMEOUT_50_PERCENT_PREV_APPEAL_BOND
			) {
				_distributeFeesToLeader(
					txId,
					amountDistributed,
					leaderTimeoutFees / 2,
					currentRoundFees.leader
				);
				feesToReturnToUser[txId] += leaderTimeoutFees / 2;
			} else if (
				currentRoundFees.roundType ==
				IFeeManager
					.RoundTypes
					.LEADER_TIMEOUT_150_PERCENT_PREV_NORMAL_ROUND
			) {
				_distributeFeesToLeader(
					txId,
					amountDistributed,
					(leaderTimeoutFees + (leaderTimeoutFees / 2)),
					currentRoundFees.leader
				);
				feesToReturnToUser[txId] += leaderTimeoutFees / 2;
				_distributeFeesToValidators(
					txId,
					amountDistributed,
					0,
					validatorTimeoutFees,
					false,
					false,
					currentRoundFees
				);
			} else if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.APPEAL_VALIDATOR_SUCCESS
			) {
				_distributeFeesToValidators(
					txId,
					amountDistributed,
					0,
					validatorTimeoutFees,
					true,
					false,
					currentRoundFees
				);
				amountDistributed += leaderTimeoutFees;
				feesToReturnToAppealer[txId][i] = IFeeManager.AppealerRewards({
					totalRewards: leaderTimeoutFees +
						currentRoundFees.appealBond,
					appealerAddress: currentRoundFees.leader
				});
			} else if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.APPEAL_VALIDATOR_UNSUCCESSFUL
			) {
				_distributeFeesToValidators(
					txId,
					amountDistributed,
					0,
					validatorTimeoutFees,
					true,
					false,
					currentRoundFees
				);
				// _addBackToUser()
				feesToReturnToUser[txId] += currentRoundFees.appealBond;
			} else if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.APPEAL_LEADER_SUCCESS
			) {
				// _distributeFeesToAppealer()
				amountDistributed += leaderTimeoutFees;
				feesToReturnToAppealer[txId][i] = IFeeManager.AppealerRewards({
					totalRewards: leaderTimeoutFees +
						currentRoundFees.appealBond,
					appealerAddress: currentRoundFees.leader
				});
			} else if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.APPEAL_LEADER_UNSUCCESSFUL
			) {
				// _addBackToUser()
				feesToReturnToUser[txId] += currentRoundFees.appealBond;
			} else if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.APPEAL_LEADER_TIMEOUT_SUCCESS
			) {
				// _distributeFeesToAppealer()
				amountDistributed += leaderTimeoutFees;
				feesToReturnToAppealer[txId][i] = IFeeManager.AppealerRewards({
					totalRewards: leaderTimeoutFees /
						2 +
						currentRoundFees.appealBond,
					appealerAddress: currentRoundFees.leader
				});
			} else if (
				currentRoundFees.roundType ==
				IFeeManager.RoundTypes.APPEAL_LEADER_TIMEOUT_UNSUCCESSFUL
			) {
				// _addBackToUser()
				feesToReturnToUser[txId] += currentRoundFees.appealBond;
			}

			while (performRotationCheck && rotation > 0) {
				--rotation;
				currentRoundFees = rotationRoundFees[txId][i][rotation];
				_distributeFeesToValidators(
					txId,
					amountDistributed,
					0,
					validatorTimeoutFees,
					true,
					false,
					currentRoundFees
				);
			}
		}

		if (amountDistributed > transactionFeeBalance[txId]) {
			revert Errors.InsufficientFeesForRound();
		}
		address[] memory validatorsToReceiveFees = new address[](
			numOfValidatorsForDistribution[txId]
		);
		uint256[] memory validatorsToReceiveFeesAmount = new uint256[](
			numOfValidatorsForDistribution[txId]
		);
		for (uint256 i = 0; i < validatorsToReceiveFees.length; i++) {
			address validator = validatorsIndexToAddress[i];
			validatorsToReceiveFees[i] = validator;
			validatorsToReceiveFeesAmount[i] = distributedFeesPerValidatorForTx[
				txId
			][validator];
		}
		_issueFeesToStakingContract(
			txId,
			validatorsToReceiveFees,
			validatorsToReceiveFeesAmount
		);
		_issueFeesBackToAppealer(txId, finalRound);
		_issueFeesBackToSender(
			txId,
			transactionFeeBalance[txId] - amountDistributed
		);
		emit IFeeManager.FeesCommitted(txId, amountDistributed);
	}

	///// INTERNAL FUNCTIONS /////

	function _updateRoundTypesBasedOnCurrentRoundResult(
		bytes32 txId,
		uint256 currentRound,
		uint256 currentRotation,
		ITransactions.ResultType currentRoundResult,
		IFeeManager.RoundTypes currentRoundType
	) internal {
		if (currentRound == 0) {
			rotationRoundFees[txId][currentRound][currentRotation]
				.roundType = currentRoundType;
			return;
		}
		uint256 lastRound = currentRound - 1;
		uint256 lastRoundRotations = rotationsUsedForTxRound[txId][lastRound] >
			1
			? rotationsUsedForTxRound[txId][lastRound] - 1
			: 0;
		IFeeManager.RoundTypes lastRoundType = rotationRoundFees[txId][
			lastRound
		][lastRoundRotations].roundType;
		ITransactions.ResultType lastRoundResult = rotationRoundFees[txId][
			lastRound
		][lastRoundRotations].result;
		if (lastRound > 0 && currentRound % 2 == 0) {
			uint256 secondLastRound = currentRound - 2;
			uint256 secondLastRoundRotations = rotationsUsedForTxRound[txId][
				secondLastRound
			] > 1
				? rotationsUsedForTxRound[txId][secondLastRound] - 1
				: 0;
			ITransactions.ResultType secondLastRoundResult = rotationRoundFees[
				txId
			][secondLastRound][secondLastRoundRotations].result;
			IFeeManager.RoundTypes secondLastRoundType = rotationRoundFees[
				txId
			][secondLastRound][secondLastRoundRotations].roundType;
			if (
				secondLastRoundType ==
				IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND &&
				(secondLastRoundResult ==
					ITransactions.ResultType.DeterministicViolation ||
					secondLastRoundResult ==
					ITransactions.ResultType.MajorityDisagree)
			) {
				if (
					(lastRoundResult ==
						ITransactions.ResultType.DeterministicViolation ||
						lastRoundResult ==
						ITransactions.ResultType.MajorityDisagree)
				) {
					rotationRoundFees[txId][secondLastRound][
						secondLastRoundRotations
					].roundType = IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND;
					rotationRoundFees[txId][lastRound][lastRoundRotations]
						.roundType = IFeeManager
						.RoundTypes
						.APPEAL_LEADER_UNSUCCESSFUL;
					rotationRoundFees[txId][currentRound][currentRotation]
						.roundType = IFeeManager
						.RoundTypes
						.SPLIT_PREV_APPEAL_BOND_ROUND;
				} else if (
					(lastRoundResult ==
						ITransactions.ResultType.MajorityAgree ||
						lastRoundResult == ITransactions.ResultType.Agree)
				) {
					rotationRoundFees[txId][secondLastRound][
						secondLastRoundRotations
					].roundType = IFeeManager.RoundTypes.SKIP_REWARDS_ROUND;
					rotationRoundFees[txId][lastRound][lastRoundRotations]
						.roundType = IFeeManager
						.RoundTypes
						.APPEAL_LEADER_SUCCESS;
					rotationRoundFees[txId][currentRound][currentRotation]
						.roundType = IFeeManager
						.RoundTypes
						.NORMAL_REWARDS_ROUND;
				} else if (
					(currentRoundResult == ITransactions.ResultType.Timeout)
				) {
					rotationRoundFees[txId][secondLastRound][
						secondLastRoundRotations
					].roundType = IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND;
					rotationRoundFees[txId][lastRound][lastRoundRotations]
						.roundType = IFeeManager
						.RoundTypes
						.APPEAL_LEADER_TIMEOUT_SUCCESS;
					rotationRoundFees[txId][currentRound][currentRotation]
						.roundType = IFeeManager
						.RoundTypes
						.LEADER_TIMEOUT_50_PERCENT;
				}
			} else if (
				secondLastRoundType ==
				IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND &&
				lastRoundType ==
				IFeeManager.RoundTypes.APPEAL_VALIDATOR_UNSUCCESSFUL
			) {
				rotationRoundFees[txId][currentRound][currentRotation]
					.roundType = IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND;
			} else if (
				secondLastRoundType ==
				IFeeManager.RoundTypes.LEADER_TIMEOUT_50_PERCENT
			) {
				if (currentRoundType == secondLastRoundType) {
					rotationRoundFees[txId][secondLastRound][
						secondLastRoundRotations
					].roundType = IFeeManager
						.RoundTypes
						.LEADER_TIMEOUT_50_PERCENT;
					rotationRoundFees[txId][lastRound][lastRoundRotations]
						.roundType = IFeeManager
						.RoundTypes
						.APPEAL_LEADER_TIMEOUT_UNSUCCESSFUL;
					rotationRoundFees[txId][currentRound][currentRotation]
						.roundType = IFeeManager
						.RoundTypes
						.LEADER_TIMEOUT_50_PERCENT_PREV_APPEAL_BOND;
				} else if (
					currentRoundType != secondLastRoundType &&
					currentRoundResult != secondLastRoundResult
				) {
					rotationRoundFees[txId][secondLastRound][
						secondLastRoundRotations
					].roundType = IFeeManager.RoundTypes.SKIP_REWARDS_ROUND;
					rotationRoundFees[txId][lastRound][lastRoundRotations]
						.roundType = IFeeManager
						.RoundTypes
						.APPEAL_LEADER_TIMEOUT_SUCCESS;
					rotationRoundFees[txId][currentRound][currentRotation]
						.roundType = IFeeManager
						.RoundTypes
						.LEADER_TIMEOUT_150_PERCENT_PREV_NORMAL_ROUND;
				}
			} else if (
				secondLastRoundType == IFeeManager.RoundTypes.EMPTY_ROUND &&
				(currentRoundResult != ITransactions.ResultType.Timeout ||
					currentRoundResult != ITransactions.ResultType.Idle) &&
				currentRoundType == IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND
			) {
				rotationRoundFees[txId][secondLastRound][
					secondLastRoundRotations
				].roundType = IFeeManager.RoundTypes.EMPTY_ROUND;
				rotationRoundFees[txId][lastRound][lastRoundRotations]
					.roundType = IFeeManager
					.RoundTypes
					.APPEAL_VALIDATOR_UNSUCCESSFUL;
				rotationRoundFees[txId][currentRound][currentRotation]
					.roundType = IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND;
				while (secondLastRound > 2 && (secondLastRound - 2) > 0) {
					secondLastRound = secondLastRound - 2;
					secondLastRoundRotations = rotationsUsedForTxRound[txId][
						secondLastRound
					] > 1
						? rotationsUsedForTxRound[txId][secondLastRound] - 1
						: 0;
					if (
						rotationRoundFees[txId][secondLastRound][
							secondLastRoundRotations
						].roundType != IFeeManager.RoundTypes.EMPTY_ROUND
					) {
						rotationRoundFees[txId][secondLastRound][
							secondLastRoundRotations
						].roundType = IFeeManager.RoundTypes.SKIP_REWARDS_ROUND;
						break;
					}
				}
			}
		} else {
			if (
				lastRoundType == IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND &&
				currentRoundResult == lastRoundResult
			) {
				rotationRoundFees[txId][lastRound][lastRoundRotations]
					.roundType = IFeeManager.RoundTypes.SKIP_REWARDS_ROUND;
				rotationRoundFees[txId][currentRound][currentRotation]
					.roundType = IFeeManager
					.RoundTypes
					.APPEAL_VALIDATOR_SUCCESS;
			} else if (
				lastRoundType == IFeeManager.RoundTypes.NORMAL_REWARDS_ROUND &&
				currentRoundResult != lastRoundResult
			) {
				rotationRoundFees[txId][currentRound][currentRotation]
					.roundType = IFeeManager
					.RoundTypes
					.APPEAL_VALIDATOR_UNSUCCESSFUL;
			} else if (
				lastRoundType ==
				IFeeManager.RoundTypes.LEADER_TIMEOUT_50_PERCENT
			) {
				rotationRoundFees[txId][currentRound][currentRotation]
					.roundType = IFeeManager
					.RoundTypes
					.APPEAL_LEADER_TIMEOUT_UNSUCCESSFUL;
			} else if (
				lastRoundType == IFeeManager.RoundTypes.EMPTY_ROUND &&
				currentRound > 1
			) {
				rotationRoundFees[txId][currentRound][currentRotation]
					.roundType = IFeeManager
					.RoundTypes
					.APPEAL_VALIDATOR_UNSUCCESSFUL;
			}
		}
	}

	function _distributeFeesToLeader(
		bytes32 txId,
		uint256 amountDistributed,
		uint256 leaderTimeoutFees,
		address leader
	) internal {
		distributedFeesPerValidatorForTx[txId][leader] += leaderTimeoutFees;
		amountDistributed += leaderTimeoutFees;
		if (distributedFeesPerValidatorForTx[txId][leader] == 0) {
			validatorsIndexToAddress[
				numOfValidatorsForDistribution[txId]
			] = leader;
			++numOfValidatorsForDistribution[txId];
		}
	}

	function _distributeFeesToValidators(
		bytes32 txId,
		uint256 amountDistributed,
		uint256 extraAmountToDistribute,
		uint256 validatorTimeoutFees,
		bool skipLeader,
		bool distrubuteToAll,
		IFeeManager.RotationRoundFees memory currentRoundFees
	) internal {
		uint256 validatorsAligned = 0;
		bool[] memory validatorsToDistributeTo = new bool[](
			currentRoundFees.validators.length
		);
		for (uint256 i = 0; i < currentRoundFees.validators.length; i++) {
			if (
				skipLeader &&
				currentRoundFees.leader == currentRoundFees.validators[i]
			) {
				continue;
			}
			if (
				_voteAlignedWithResult(
					currentRoundFees.validatorsVotes[i],
					currentRoundFees.result
				) || distrubuteToAll
			) {
				validatorsAligned++;
				validatorsToDistributeTo[i] = true;
				if (
					distributedFeesPerValidatorForTx[txId][
						currentRoundFees.validators[i]
					] == 0
				) {
					validatorsIndexToAddress[
						numOfValidatorsForDistribution[txId]
					] = currentRoundFees.validators[i];
					++numOfValidatorsForDistribution[txId];
				}
			}
		}
		uint256 totalAmountToDistribute = ((validatorTimeoutFees *
			currentRoundFees.validators.length) + extraAmountToDistribute);
		amountDistributed += totalAmountToDistribute;
		uint256 feeToDistributePerValidator = (totalAmountToDistribute * 1e18) /
			(validatorsAligned * 1e18);
		for (uint256 i = 0; i < currentRoundFees.validators.length; i++) {
			if (validatorsToDistributeTo[i]) {
				distributedFeesPerValidatorForTx[txId][
					currentRoundFees.validators[i]
				] += feeToDistributePerValidator;
			}
		}
	}

	function _issueFeesBackToSender(bytes32 txId, uint256 amount) internal {
		transactionFeeBalance[txId] -= amount;
		if (lastTransactionTopUpSender[txId] != address(0)) {
			(bool success, ) = lastTransactionTopUpSender[txId].call{
				value: amount
			}("");
			if (!success) {
				revert Errors.FailedTransferCall();
			}
			emit IFeeManager.FeesIssuedBack(
				txId,
				lastTransactionTopUpSender[txId],
				amount
			);
		} else {
			revert Errors.NoSenderForTransaction();
		}
	}

	function _issueFeesToStakingContract(
		bytes32 txId,
		address[] memory validators,
		uint256[] memory amount
	) internal {
		// TODO: Implement
	}

	function _issueFeesBackToAppealer(
		bytes32 txId,
		uint256 finalRound
	) internal {
		address appealer;
		uint256 amount;
		for (uint256 i = 1; i < finalRound; i++) {
			IFeeManager.AppealerRewards
				memory appealerRewards = feesToReturnToAppealer[txId][i];
			if (appealerRewards.appealerAddress != address(0)) {
				if (appealer == address(0)) {
					appealer = appealerRewards.appealerAddress;
					amount = appealerRewards.totalRewards;
				} else if (appealerRewards.appealerAddress != appealer) {
					(bool success, ) = payable(appealer).call{ value: amount }(
						""
					);
					if (!success) {
						revert Errors.FailedTransferCall();
					}
					appealer = appealerRewards.appealerAddress;
					amount = appealerRewards.totalRewards;
				} else {
					amount += appealerRewards.totalRewards;
				}
			}
		}
		if (amount > 0) {
			(bool success, ) = payable(appealer).call{ value: amount }("");
			if (!success) {
				revert Errors.FailedTransferCall();
			}
		}
	}

	function _voteAlignedWithResult(
		ITransactions.VoteType vote,
		ITransactions.ResultType result
	) internal pure returns (bool) {
		bool aligned = false;
		if (vote == ITransactions.VoteType.Agree) {
			aligned =
				result == ITransactions.ResultType.Agree ||
				result == ITransactions.ResultType.MajorityAgree;
		} else if (vote == ITransactions.VoteType.Disagree) {
			aligned =
				result == ITransactions.ResultType.Disagree ||
				result == ITransactions.ResultType.MajorityDisagree;
		} else if (vote == ITransactions.VoteType.Timeout) {
			aligned = result == ITransactions.ResultType.Timeout;
		} else if (vote == ITransactions.VoteType.DeterministicViolation) {
			aligned = result == ITransactions.ResultType.DeterministicViolation;
		}
		return aligned;
	}

	function _addFeesDistribution(
		bytes32 txId,
		IFeeManager.FeesDistribution memory _feesDistribution
	) internal {
		IFeeManager.FeesDistribution
			storage feesDistribution = feesDistributionForTx[txId];
		feesDistribution.leaderTimeout = _feesDistribution.leaderTimeout;
		feesDistribution.validatorsTimeout = _feesDistribution
			.validatorsTimeout;
		feesDistribution.appealRounds += _feesDistribution.appealRounds;
		feesDistribution.rollupStorageFee += _feesDistribution.rollupStorageFee;
		feesDistribution.rollupGenVMFee += _feesDistribution.rollupGenVMFee;
		feesDistribution.totalMessageFees += _feesDistribution.totalMessageFees;
		feesDistribution.rotations = _concatArrays(
			feesDistribution.rotations,
			_feesDistribution.rotations
		);
	}

	function _checkFeesForNewRound(
		bytes32 _txId,
		uint256 _round
	) internal view returns (bool feesForNewRoundApproved) {
		_round = _round % 2 == 0 ? _round : (_round + 1);
		feesForNewRoundApproved =
			_round <= (feesDistributionForTx[_txId].appealRounds * 2);
	}

	function _topUpFees(
		bytes32 _txId,
		IFeeManager.FeesDistribution memory _feesDistribution,
		uint256 _amount,
		bool _performFeeChecks,
		address _sender
	) internal {
		if (_performFeeChecks) {
			uint256 feesToBeUsed = _calculateRoundFees(
				_txId,
				_feesDistribution,
				_amount,
				0
			);
			if (feesToBeUsed > _amount) {
				revert Errors.InsufficientFeesForRound();
			}
		}
		_addFeesDistribution(_txId, _feesDistribution);
		transactionFeeBalance[_txId] += _amount;
		if (currentRoundForTx[_txId] == 0) {
			currentRoundForTx[_txId] = 1;
		}
		lastTransactionTopUpSender[_txId] = _sender;
		emit IFeeManager.FeesDeposited(_txId, _sender, _amount);
	}

	function _calculateRoundFees(
		bytes32 txId,
		IFeeManager.FeesDistribution memory _feesDistribution,
		uint256 _numOfValidators,
		uint256 round
	) internal view returns (uint256 totalFeesToPay) {
		if (round == 0) {
			uint256 index = _validatorIndex(_numOfValidators);
			uint256 numOfValidators = VALIDATORS_PER_ROUND[index];
			if (numOfValidators != _numOfValidators) {
				revert Errors.InvalidNumOfValidators();
			}
			// Calculate if total fees for all rounds are valid
			if (
				_feesDistribution.appealRounds !=
				_feesDistribution.rotations.length - 1
			) {
				revert Errors.InvalidAppealRounds();
			}
			totalFeesToPay = _calculateFees(
				_feesDistribution,
				index,
				VALIDATORS_PER_ROUND
			);
		} else {
			IFeeManager.FeesDistribution
				memory feesDistribution = feesDistributionForTx[txId];
			// `round` is the raw consensus round (0, 2, 4, ... for normal
			// rounds), while `rotations` is indexed by normal-round ordinal.
			uint256 rotationsIndex = round / 2;
			totalFeesToPay = _calculateFeeForARound(
				VALIDATORS_PER_ROUND[round],
				feesDistribution.rotations[rotationsIndex] + 1,
				feesDistribution.leaderTimeout,
				feesDistribution.validatorsTimeout
			);
		}
	}

	function _calculateFees(
		IFeeManager.FeesDistribution memory _feesDistribution,
		uint256 _index,
		uint[] memory _numOfValidatorsForEachRound
	) internal pure returns (uint256 calculatedFees) {
		// First round
		calculatedFees = _calculateFeeForARound(
			_numOfValidatorsForEachRound[_index],
			_feesDistribution.rotations[0] + 1,
			_feesDistribution.leaderTimeout,
			_feesDistribution.validatorsTimeout
		);
		uint256 rotationsIndex = 1;
		uint256 rotationsThisRound = 1;
		// Appeal rounds and normal rounds with rotations
		for (uint256 i = 0; i < _feesDistribution.appealRounds * 2; i++) {
			uint256 roundValidators = _numOfValidatorsForEachRound[
				_index + i + 1
			];
			if (i % 2 == 1) {
				// if i is odd, we are in a normal round with rotations
				// rotations index should not exceed the items in the rotations array
				if (rotationsIndex < _feesDistribution.rotations.length) {
					rotationsThisRound = _feesDistribution.rotations[
						rotationsIndex++
					];
				}
			} else {
				// if i is even, we are in an appeal round, no rotations
				rotationsThisRound = 0;
			}

			// Calculate fees for the round (appeal rounds and normal rounds with rotations)
			// add fees for the round to the total fees
			calculatedFees += _calculateFeeForARound(
				roundValidators,
				rotationsThisRound + 1,
				_feesDistribution.leaderTimeout,
				_feesDistribution.validatorsTimeout
			);
		}
	}

	function _calculateFeeForARound(
		uint256 _numOfValidators,
		uint256 _rotations,
		uint256 _leaderTimeout,
		uint256 _validatorsTimeout
	) internal pure returns (uint256 calculatedFees) {
		calculatedFees =
			(_numOfValidators * _rotations * _validatorsTimeout) +
			(_rotations * _leaderTimeout);
	}

	function _validatorIndex(
		uint256 _numOfValidators
	) internal view returns (uint) {
		uint256 index = 0;
		while (VALIDATORS_PER_ROUND[index] < _numOfValidators) {
			index++;
		}
		return index;
	}

	function _concatArrays(
		uint256[] memory _array1,
		uint256[] memory _array2
	) internal pure returns (uint256[] memory) {
		uint256[] memory result = new uint256[](
			_array1.length + _array2.length
		);
		for (uint256 i = 0; i < _array1.length; i++) {
			result[i] = _array1[i];
		}
		for (uint256 i = 0; i < _array2.length; i++) {
			result[_array1.length + i] = _array2[i];
		}
		return result;
	}
}
