# database_handler/contract_snapshot.py
from .models import CurrentState
from sqlalchemy.orm import Session
from typing import Optional, Dict
import base64
import json


class ContractSnapshot:
    """
    Warning: if you initialize this class with a contract_address:
    - The contract_address must exist in the database.
    - `self.contract_data`, `self.contract_code` and `self.states` will be loaded from the database **only once** at initialization.
    """

    contract_address: str
    contract_code: str
    balance: int
    states: Dict[str, Dict[str, str]]

    def __init__(self, contract_address: str | None, session: Session):
        if contract_address is not None:
            self.contract_address = contract_address

            contract_account = self._load_contract_account(session)
            self.contract_data = contract_account.data
            self.contract_code = self.contract_data["code"]
            self.balance = contract_account.balance

            if ("accepted" in self.contract_data["state"]) and (
                isinstance(self.contract_data["state"]["accepted"], dict)
            ):
                self.states = self.contract_data["state"]
            else:
                # Convert old state format
                self.states = {"accepted": self.contract_data["state"], "finalized": {}}

    def to_dict(self):
        return {
            "contract_address": (
                self.contract_address if self.contract_address else None
            ),
            "contract_code": self.contract_code if self.contract_code else None,
            "states": self.states if self.states else {"accepted": {}, "finalized": {}},
        }

    @classmethod
    def from_dict(cls, input: dict | None) -> Optional["ContractSnapshot"]:
        if input:
            instance = cls.__new__(cls)
            instance.contract_address = input.get("contract_address", None)
            instance.contract_code = input.get("contract_code", None)
            instance.states = input.get("states", {"accepted": {}, "finalized": {}})
            return instance
        else:
            return None

    def _load_contract_account(self, session: Session) -> CurrentState:
        """Load and return the current state of the contract from the database."""
        result = (
            session.query(CurrentState)
            .filter(CurrentState.id == self.contract_address)
            .populate_existing()  # Force refresh from database even if cached
            .one_or_none()
        )

        if result is None:
            raise Exception(f"Contract {self.contract_address} not found")
        
        # Handle legacy JSON string data and validate deployment
        if isinstance(result.data, str):
            result.data = json.loads(result.data)

        if not result.data:
            raise Exception(f"Contract {self.contract_address} not deployed")

        return result

    def extract_deployed_code_b64(self) -> Optional[str]:
        """Extract the deployed contract code as base64 from this instance's state.

        This reads the code slot key, fetches the stored blob, validates and
        slices out the code payload, and returns it base64-encoded. Returns None
        if missing/invalid.
        """
        # Import here to avoid circular dependencies at module import time
        from backend.node.genvm.origin.base_host import get_code_slot

        accepted = self.states.get("accepted") or {}

        try:
            code_slot_b64 = base64.b64encode(get_code_slot()).decode("ascii")
            stored = accepted.get(code_slot_b64)
            if not stored:
                return None

            raw = base64.b64decode(stored, validate=True)
            code_len = int.from_bytes(raw[0:4], byteorder="little", signed=False)
            code_bytes = raw[4 : 4 + code_len]
            return base64.b64encode(code_bytes).decode("ascii")
        except Exception:
            return None
