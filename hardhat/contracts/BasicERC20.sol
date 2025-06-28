// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { ERC20 } from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import { ERC20Burnable } from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Burnable.sol";
import { ERC20Pausable } from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Pausable.sol";

/**
 * This file was generated with Openzeppelin Wizard and later modified.
 * GO TO: https://wizard.openzeppelin.com/#erc20
 */
contract BasicERC20 is ERC20, ERC20Burnable, ERC20Pausable, Ownable {
	constructor(
		string memory name,
		string memory symbol,
		address initialOwner,
		uint256 initialSupply
	) ERC20(name, symbol) Ownable(initialOwner) {
		_mint(initialOwner, initialSupply);
	}

	function pause() external onlyOwner {
		_pause();
	}

	function unpause() external onlyOwner {
		_unpause();
	}

	function mint(address to, uint256 amount) external onlyOwner {
		_mint(to, amount);
	}

	function _update(
		address from,
		address to,
		uint256 value
	) internal override(ERC20, ERC20Pausable) {
		super._update(from, to, value);
	}
}