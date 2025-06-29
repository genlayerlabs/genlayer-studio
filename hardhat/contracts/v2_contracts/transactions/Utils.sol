// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { ITransactions } from "./interfaces/ITransactions.sol";
import { Errors } from "../utils/Errors.sol";

contract Utils {
	function checkStatus(
		ITransactions.TransactionStatus _status,
		ITransactions.TransactionStatus _allowedStatus
	) external pure {
		if (_status != _allowedStatus) {
			revert Errors.InvalidTransactionStatus();
		}
	}

	function createDefaultUpdateInfo()
		external
		pure
		returns (ITransactions.UpdateTransactionInfo memory)
	{
		return
			ITransactions.UpdateTransactionInfo({
				id: bytes32(0),
				activator: address(0),
				timestamps: ITransactions.Timestamps({
					created: 0,
					pending: 0,
					activated: 0,
					proposed: 0,
					committed: 0,
					lastVote: 0
				}),
				consumedValidators: new address[](0),
				roundData: ITransactions.RoundData(
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
				),
				round: 0
			});
	}
}