// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import { UpgradeableBeacon } from "@openzeppelin/contracts/proxy/beacon/UpgradeableBeacon.sol";
import { BeaconProxy } from "@openzeppelin/contracts/proxy/beacon/BeaconProxy.sol";
import "./GhostBlueprint.sol";
contract GhostFactory is
	Initializable,
	Ownable2StepUpgradeable,
	ReentrancyGuardUpgradeable,
	AccessControlUpgradeable
{
	address public ghostBlueprint;
	address public ghostBeaconProxy;
	address public ghostManager;
	address public genConsensus;

	mapping(address => bool) public ghostContracts;
	address public latestGhost;

	error CallerNotConsensus();

	receive() external payable {}

	function initialize() public initializer {
		__Ownable_init(msg.sender);
		__Ownable2Step_init();
		__ReentrancyGuard_init();
		__AccessControl_init();
	}

	function createGhost() external payable onlyGenConsensus returns (address) {
		BeaconProxy beacon = new BeaconProxy(
			ghostBeaconProxy,
			abi.encodeWithSelector(
				GhostBlueprint.initialize.selector,
				address(this)
			)
		);
		emit GhostCreated(address(beacon));
		GhostBlueprint(payable(address(beacon))).transferOwnership(msg.sender);
		latestGhost = address(beacon);
		ghostContracts[address(beacon)] = true;
		return address(beacon);
	}

	function deployNewBeaconProxy() external onlyOwner {
		UpgradeableBeacon beaconProxy = new UpgradeableBeacon(
			ghostBlueprint,
			address(this)
		);
		ghostBeaconProxy = address(beaconProxy);
	}

	function setGhostBlueprint(address _ghostBlueprint) external onlyOwner {
		ghostBlueprint = _ghostBlueprint;
	}

	function isGhost(address _ghost) external view returns (bool) {
		return ghostContracts[_ghost];
	}

	function upgradeBeacon(address newImplementation) external onlyOwner {
		UpgradeableBeacon(ghostBeaconProxy).upgradeTo(newImplementation);
		emit BeaconUpgraded(newImplementation);
	}

	function setGenConsensus(address _genConsensus) external onlyOwner {
		require(_genConsensus != address(0), "Invalid address");
		genConsensus = _genConsensus;
		emit GenConsensusSet(_genConsensus);
	}

	function setGhostManager(address _ghostManager) external onlyOwner {
		require(_ghostManager != address(0), "Invalid address");
		ghostManager = _ghostManager;
		emit GhostManagerSet(_ghostManager);
	}

	modifier onlyGenConsensus() {
		if (msg.sender != genConsensus) {
			revert CallerNotConsensus();
		}
		_;
	}

	event GhostCreated(address ghostAddress);
	event BeaconUpgraded(address indexed implementation);
	event GenConsensusSet(address indexed genConsensus);
	event GhostManagerSet(address indexed ghostManager);
}