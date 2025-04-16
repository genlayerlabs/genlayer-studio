from typing import List, Tuple, Dict
from backend.database_handler.types import ConsensusData
from backend.node.types import Receipt
from backend.consensus.helpers.vrf import get_validators_for_transaction


class ValidatorManagement:
    @staticmethod
    def get_validators_from_consensus_data(
        all_validators: List[dict],
        consensus_data: ConsensusData,
        include_leader: bool,
    ) -> Tuple[List[dict], Dict[str, dict]]:
        """
        Get validators from consensus data.

        Args:
            all_validators (List[dict]): List of all validators.
            consensus_data (ConsensusData): Data related to the consensus process.
            include_leader (bool): Whether to get the leader in the validator set.

        Returns:
            Tuple[List[dict], Dict[str, dict]]: A tuple containing:
                - List of validators involved in the consensus process
                - Dictionary mapping addresses to validators not used in the consensus process
        """
        validator_map = {
            validator["address"]: validator for validator in all_validators
        }

        if include_leader:
            receipt_addresses = [consensus_data.leader_receipt.node_config["address"]]
        else:
            receipt_addresses = []

        receipt_addresses += [
            receipt.node_config["address"] for receipt in consensus_data.validators
        ]

        validators = [
            validator_map.pop(receipt_address)
            for receipt_address in receipt_addresses
            if receipt_address in validator_map
        ]

        return validators, validator_map

    @staticmethod
    def get_used_leader_addresses_from_consensus_history(
        consensus_history: dict,
        current_leader_receipt: Receipt | None = None,
    ) -> set[str]:
        """
        Get the used leader addresses from the consensus history.

        Args:
            consensus_history (dict): Dictionary of consensus rounds results and status changes.
            current_leader_receipt (Receipt | None): Current leader receipt.

        Returns:
            set[str]: Set of used leader addresses.
        """
        used_leader_addresses = set()
        if "consensus_results" in consensus_history:
            for consensus_round in consensus_history["consensus_results"]:
                leader_receipt = consensus_round["leader_result"]
                if leader_receipt:
                    used_leader_addresses.update(
                        [leader_receipt["node_config"]["address"]]
                    )

        if current_leader_receipt:
            used_leader_addresses.update(
                [current_leader_receipt.node_config["address"]]
            )

        return used_leader_addresses

    @staticmethod
    def add_new_validator(
        all_validators: List[dict],
        validators: List[dict],
        leader_addresses: set[str],
    ) -> List[dict]:
        """
        Add a new validator to the list of validators.

        Args:
            all_validators (List[dict]): List of all validators.
            validators (List[dict]): List of validators.
            leader_addresses (set[str]): Set of leader addresses.

        Returns:
            List[dict]: Updated list of validators.

        Raises:
            ValueError: If no more validators are available to add.
        """
        if len(leader_addresses) + len(validators) >= len(all_validators):
            raise ValueError("No more validators found to add a new validator")

        addresses = {validator["address"] for validator in validators}
        addresses.update(leader_addresses)

        not_used_validators = [
            validator
            for validator in all_validators
            if validator["address"] not in addresses
        ]

        new_validator = get_validators_for_transaction(not_used_validators, 1)
        return new_validator + validators

    @staticmethod
    def get_extra_validators(
        all_validators: List[dict],
        consensus_history: dict,
        consensus_data: ConsensusData,
        appeal_failed: int,
    ):
        """
        Get extra validators for the appeal process according to the following formula:
        - when appeal_failed = 0, add n + 2 validators
        - when appeal_failed > 0, add (2 * appeal_failed * n + 1) + 2 validators
        Note that for appeal_failed > 0, the returned set contains the old validators
        from the previous appeal round and new validators.

        Selection of the extra validators:
        appeal_failed | PendingState | Reused validators | Extra selected     | Total
                      | validators   | from the previous | validators for the | validators
                      |              | appeal round      | appeal             |
        ----------------------------------------------------------------------------------
               0      |       n      |          0        |        n+2         |    2n+2
               1      |       n      |        n+2        |        n+1         |    3n+3
               2      |       n      |       2n+3        |         2n         |    5n+3
               3      |       n      |       4n+3        |         2n         |    7n+3
                              └───────┬──────┘  └─────────┬────────┘
                                      │                   |
        Validators after the ◄────────┘                   └──► Validators during the appeal
        appeal. This equals                                    for appeal_failed > 0
        the Total validators                                   = (2*appeal_failed*n+1)+2
        of the row above,                                      This is the formula from
        and are in consensus_data.                             above and it is what is
        For appeal_failed > 0                                  returned by this function
        = (2*appeal_failed-1)*n+3
        This is used to calculate n

        Args:
            all_validators (List[dict]): List of all validators.
            consensus_history (dict): Dictionary of consensus rounds results and status changes.
            consensus_data (ConsensusData): Data related to the consensus process.
            appeal_failed (int): Number of times the appeal has failed.

        Returns:
            list: List of current validators.
            list: List of extra validators.
        """
        # Get current validators and a dictionary mapping addresses to validators not used in the consensus process
        current_validators, validator_map = (
            ValidatorManagement.get_validators_from_consensus_data(
                all_validators, consensus_data, False
            )
        )

        # Remove used leaders from validator_map
        used_leader_addresses = (
            ValidatorManagement.get_used_leader_addresses_from_consensus_history(
                consensus_history
            )
        )
        for used_leader_address in used_leader_addresses:
            if used_leader_address in validator_map:
                validator_map.pop(used_leader_address)

        # Set not_used_validators to the remaining validators in validator_map
        not_used_validators = list(validator_map.values())

        if len(not_used_validators) == 0:
            raise ValueError("No validators found")

        nb_current_validators = len(current_validators) + 1
        if appeal_failed == 0:
            # Calculate extra validators when no appeal has failed
            extra_validators = get_validators_for_transaction(
                not_used_validators, nb_current_validators + 2
            )
        elif appeal_failed == 1:
            # Calculate extra validators when one appeal has failed
            n = (nb_current_validators - 2) // 2
            extra_validators = get_validators_for_transaction(
                not_used_validators, n + 1
            )
            extra_validators = current_validators[n - 1 :] + extra_validators
        else:
            # Calculate extra validators when more than one appeal has failed
            n = (nb_current_validators - 3) // (2 * appeal_failed - 1)
            extra_validators = get_validators_for_transaction(
                not_used_validators, 2 * n
            )
            extra_validators = current_validators[n - 1 :] + extra_validators

        return current_validators, extra_validators
