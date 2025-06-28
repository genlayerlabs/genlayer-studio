// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import "./transactions/interfaces/ITransactions.sol";
import "./interfaces/IGenConsensus.sol";
contract Messages is
	Initializable,
	Ownable2StepUpgradeable,
	ReentrancyGuardUpgradeable,
	AccessControlUpgradeable
{
	event MessagesOnAcceptance(bytes32 indexed tx_id);
	event MessagesOnFinalization(bytes32 indexed tx_id);
	event GenTransactionsSet(address indexed genTransactions);
	event GenConsensusSet(address indexed genConsensus);

	ITransactions public genTransactions;
	IGenConsensus public genConsensus;
	mapping(address => uint) public ghostNonce;
	error CallerNotConsensus();

	receive() external payable {}

	function initialize() public initializer {
		__Ownable_init(msg.sender);
		__Ownable2Step_init();
		__ReentrancyGuard_init();
		__AccessControl_init();
	}

	/**
	 * @notice Emits messages marked for acceptance for a given transaction
	 * @dev Only callable by consensus contract. Processes messages marked with onAcceptance=true
	 * @param _tx_id The ID of the transaction whose messages should be emitted
	 */
	function emitMessagesOnAcceptance(bytes32 _tx_id) external onlyConsensus {
		ITransactions.Transaction memory transaction = genTransactions
			.getTransaction(_tx_id);

		for (uint i = 0; i < transaction.messages.length; i++) {
			IMessages.SubmittedMessage memory message = transaction.messages[i];
			if (!message.onAcceptance) {
				continue;
			}
			_handleMessage(message);

			// Update the count of already emitted messages
			emit MessagesOnAcceptance(_tx_id);
		}
	}

	/**
	 * @notice Emits messages marked for finalization for a given transaction
	 * @dev Only callable by consensus contract. Processes messages marked with onAcceptance=false
	 * @param _tx_id The ID of the transaction whose messages should be emitted
	 */
	function emitMessagesOnFinalization(bytes32 _tx_id) external onlyConsensus {
		ITransactions.Transaction memory transaction = genTransactions
			.getTransaction(_tx_id);

		for (uint i = 0; i < transaction.messages.length; i++) {
			IMessages.SubmittedMessage memory message = transaction.messages[i];
			if (message.onAcceptance) {
				continue;
			}
			_handleMessage(message);

			// Update the count of already emitted messages
			emit MessagesOnFinalization(_tx_id);
		}
	}

	function executeMessage(
		IMessages.SubmittedMessage memory message
	) external onlyConsensus {
		_handleMessage(message);
	}

	function _handleMessage(
		IMessages.SubmittedMessage memory message
	) internal {
		bytes memory messageBytes = message.data;
		// Decode the message data
		(address to, bytes memory data) = abi.decode(
			messageBytes,
			(address, bytes)
		);
		bytes32 msgId = keccak256(abi.encode(to, data));
		// Call handleOp on the recipient
		bool success = genConsensus.executeMessage(
			message.recipient,
			0,
			abi.encodeWithSignature(
				"handleOp(address,bytes32,uint256,bytes)",
				to,
				msgId,
				ghostNonce[message.recipient] + 1,
				data
			)
		);
		require(success, "handleOp call failed");
		ghostNonce[message.recipient]++;
		if (message.onAcceptance) {
			emit MessagesOnAcceptance(msgId);
		} else {
			emit MessagesOnFinalization(msgId);
		}
	}

	function _handleMessagePacked(
		IMessages.SubmittedMessage memory message
	) internal {
		// Extract the first 20 bytes for address (without using slice)
		address to;
		bytes memory data;
		bytes memory messageBytes = message.data;
		assembly {
			// Load the first 20 bytes into 'to' - corrected offset
			// We need to right-shift by 12 bytes (96 bits) to get the correct 20 bytes
			to := shr(96, mload(add(messageBytes, 0x20)))

			// Create a new bytes array for the remaining data
			let remaining_length := sub(mload(messageBytes), 20)
			data := mload(0x40) // get free memory pointer
			mstore(data, remaining_length) // store length
			mstore(0x40, add(add(data, 0x20), remaining_length)) // update free memory pointer

			// Copy remaining bytes
			let srcPtr := add(add(messageBytes, 0x20), 20)
			let destPtr := add(data, 0x20)
			for {
				let i := 0
			} lt(i, remaining_length) {
				i := add(i, 32)
			} {
				mstore(add(destPtr, i), mload(add(srcPtr, i)))
			}
		}
		bytes32 msgId = keccak256(abi.encode(to, data));

		// Call handleOp on the recipient
		(bool success, ) = message.recipient.call{ value: 0 }(
			abi.encodeWithSignature(
				"handleOp(address,bytes32,bytes)",
				to,
				msgId,
				data
			)
		);

		require(success, "handleOp call failed");
	}

	function setGenTransactions(address _genTransactions) external onlyOwner {
		genTransactions = ITransactions(_genTransactions);
		emit GenTransactionsSet(_genTransactions);
	}

	function setGenConsensus(address _genConsensus) external onlyOwner {
		genConsensus = IGenConsensus(_genConsensus);
		emit GenConsensusSet(_genConsensus);
	}

	modifier onlyConsensus() {
		if (msg.sender != address(genConsensus)) {
			revert CallerNotConsensus();
		}
		_;
	}
}