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

    def to_dict(self, truncate_large_fields=False):
        return {
            "votes": self.votes,
            "leader_receipt": (
                [
                    receipt.to_dict(truncate_large_fields=truncate_large_fields)
                    for receipt in self.leader_receipt
                ]
                if self.leader_receipt
                else None
            ),
            "validators": (
                [
                    receipt.to_dict(truncate_large_fields=truncate_large_fields)
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
