from dataclasses import dataclass
from backend.node.types import Receipt
from typing import Optional


@dataclass
class ConsensusData:
    votes: dict[str, str]
    leader_receipt: (
        list[Receipt] | None
    )  # first item is leader function, second item is validator function
    validators: list[Receipt] | None = None

    def to_dict(self, strip_contract_state: bool = True):
        """Convert ConsensusData to dict.

        Args:
            strip_contract_state: If True (default), removes contract_state from receipts
                                 to save database storage. Contract state is persisted in
                                 CurrentState table and doesn't need duplication here.
        """
        return {
            "votes": self.votes,
            "leader_receipt": (
                [
                    receipt.to_dict(strip_contract_state=strip_contract_state)
                    for receipt in self.leader_receipt
                ]
                if self.leader_receipt
                else None
            ),
            "validators": (
                [
                    receipt.to_dict(strip_contract_state=strip_contract_state)
                    for receipt in self.validators
                ]
                if self.validators
                else []
            ),
        }

    @classmethod
    def from_dict(cls, input: dict | None) -> Optional["ConsensusData"]:
        if input:
            return cls(
                votes=input.get("votes", {}),
                leader_receipt=(
                    [
                        Receipt.from_dict(receipt)
                        for receipt in input.get("leader_receipt")
                    ]
                    if input.get("leader_receipt")
                    else None
                ),
                validators=[
                    Receipt.from_dict(validator)
                    for validator in (input.get("validators", None) or [])
                ],
            )
        else:
            return None
