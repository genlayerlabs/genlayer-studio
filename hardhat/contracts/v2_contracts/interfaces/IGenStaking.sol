// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IGenStaking {
	/**
	 * @notice Gets the validator that should activate a transaction for a specific recipient
	 * @param _randomSeed A random seed used to select the validator
	 * @return validator The address of the selected validator
	 */
	function getActivatorForSeed(
		bytes32 _randomSeed
	) external view returns (address validator);

	/**
	 * @notice Gets the validators that should participate in a transaction
	 * @param _randomSeed A random seed used to select the validators
	 * @param numValidators The number of validators to select
	 * @return validators The addresses of the selected validators
	 * @param consumedValidators The addresses of the validators that have already participated in the transaction
	 */
	function getValidatorsForTx(
		bytes32 _randomSeed,
		uint256 numValidators,
		address[] memory consumedValidators
	) external view returns (address[] memory validators, uint256 leaderIndex);

	/**
	 * @notice Gets the length of the validators that should participate in a transaction
	 */
	function getValidatorsLen() external view returns (uint256);

	/**
	 * @notice Gets the validator at a specific index
	 * @param index The index of the validator
	 */
	function getValidatorsItem(uint256 index) external view returns (address);

	/**
	 * @notice Slashes a validator
	 * @param _validator The address of the validator to slash
	 */
	function validatorSlash(
		address _validator,
		uint256 _epoch,
		bool _deterministic
	) external;

	/**
	 * @notice Gets the number of validators that have been banned
	 */
	function getValidatorBansCount() external view returns (uint256);

	function ban(address _validator, uint256 _epoch) external;

	function slash(address _validator, uint256 _slashingPercentage) external;

	function getNextValidators(
		bytes32 _randomSeed,
		uint256 _numberOfValidators,
		address[] memory _consumedValidators,
		bool _isWeightedSelection
	) external view returns (address[] memory);
}