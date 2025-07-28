// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IGhostFactory
/// @notice Interface for creating and managing ghost contracts in the GenVM system
interface IGhostFactory {
	/// @notice Creates a new ghost contract
	/// @return The address where the contract will be deployed
	function createGhost() external payable returns (address);

	/// @notice Checks if an address is a ghost contract
	/// @param contractAddress The address to check
	/// @return bool True if the address is a ghost contract
	function isGhost(address contractAddress) external view returns (bool);

	/// @notice Returns the latest ghost contract address
	/// @return The address of the latest ghost contract
	function latestGhost() external view returns (address);
}