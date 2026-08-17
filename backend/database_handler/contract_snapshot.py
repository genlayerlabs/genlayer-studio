# database_handler/contract_snapshot.py
from .models import CurrentState
from .errors import ContractNotFoundError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Optional, Dict
import base64
import json


class ContractSnapshot:
    """
    Warning: if you initialize this class with a contract_address:
    - The contract_address must exist in the database.
    - `self.contract_data` and `self.states` will be loaded from the database **only once** at initialization.
    """

    contract_address: str
    balance: int
    states: Dict[str, Dict[str, str]]

    def __init__(self, contract_address: str | None, session: Session):
        if contract_address is not None:
            self.contract_address = contract_address

            contract_account = self._load_contract_account(session)
            self.contract_data = contract_account.data
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
            "states": self.states if self.states else {"accepted": {}, "finalized": {}},
            "balance": (
                int(b) if (b := getattr(self, "balance", None)) is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, input: dict | None) -> Optional["ContractSnapshot"]:
        if input:
            instance = cls.__new__(cls)
            instance.contract_address = input.get("contract_address", None)
            instance.states = input.get("states", {"accepted": {}, "finalized": {}})
            raw_balance = input.get("balance")
            instance.balance = int(raw_balance) if raw_balance is not None else None
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
            raise ContractNotFoundError(self.contract_address)

        # Handle legacy JSON string data and validate deployment
        if isinstance(result.data, str):
            result.data = json.loads(result.data)

        if not result.data:
            raise ContractNotFoundError(
                self.contract_address, f"Contract {self.contract_address} not deployed"
            )

        return result

    def extract_deployed_code_b64(self) -> Optional[str]:
        """Extract the deployed contract code as base64 from this instance's state.

        This reads the code slot key, fetches the stored blob, validates and
        slices out the code payload, and returns it base64-encoded. Returns None
        if missing/invalid.
        """
        accepted = self.states.get("accepted") or {}

        try:
            stored = accepted.get(_code_slot_b64())
            if not stored:
                return None
            return _decode_code_payload(stored)
        except Exception:
            return None


def _code_slot_b64() -> str:
    """Base64 of the deterministic storage slot the deployed code lives in."""
    # Import here to avoid circular dependencies at module import time
    from backend.node.genvm import get_code_slot

    return base64.b64encode(get_code_slot()).decode("ascii")


def _decode_code_payload(stored: str) -> Optional[str]:
    """Slice the code out of a stored slot blob and re-encode it as base64.

    The blob is a 4-byte little-endian length prefix followed by the code.
    """
    raw = base64.b64decode(stored, validate=True)
    code_len = int.from_bytes(raw[0:4], byteorder="little", signed=False)
    code_bytes = raw[4 : 4 + code_len]
    return base64.b64encode(code_bytes).decode("ascii")


def fetch_deployed_code_b64(session: Session, contract_address: str) -> Optional[str]:
    """Read just the deployed code, without loading the contract's whole state.

    ``ContractSnapshot`` pulls the entire ``data`` JSONB — every storage slot
    the contract owns — in order to read one deterministic slot out of it. For a
    contract holding a large vector store that is a big fetch and deserialize
    per call, which matters because ``gen_getContractCode`` is polled heavily by
    batch tooling. Extracting the slot in SQL keeps the state off the wire and
    out of Python.

    Postgres still has to detoast the JSONB server-side, so this narrows the
    transfer and parse cost rather than eliminating the read entirely.

    Raises ContractNotFoundError when the contract is absent or undeployed, and
    returns None when the contract exists but holds no code.
    """
    slot = _code_slot_b64()

    row = session.execute(
        select(
            func.jsonb_typeof(CurrentState.data).label("data_kind"),
            func.jsonb_typeof(CurrentState.data["state"]).label("state_kind"),
            CurrentState.data["state"]["accepted"][slot].astext.label("nested"),
            CurrentState.data["state"][slot].astext.label("flat"),
        ).where(CurrentState.id == contract_address)
    ).one_or_none()

    if row is None:
        raise ContractNotFoundError(contract_address)

    if row.data_kind != "object" or row.state_kind is None:
        # Legacy rows store `data` as a JSON string scalar, and undeployed ones
        # store an empty object with no `state` key. Both are rare and fiddly,
        # so hand them to the original path rather than reimplementing its error
        # handling in SQL.
        return ContractSnapshot(contract_address, session).extract_deployed_code_b64()

    # Current rows nest slots under `state.accepted`; the pre-migration format
    # put them directly under `state`.
    stored = row.nested if row.nested is not None else row.flat
    if not stored:
        return None

    try:
        return _decode_code_payload(stored)
    except Exception:
        return None
