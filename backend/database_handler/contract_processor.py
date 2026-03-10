# database_handler/contract_processor.py
from .models import CurrentState
from .contract_snapshot import ContractSnapshot
from sqlalchemy.orm import Session


class ContractProcessor:
    """
    This class is used for updating the contract's data in the database.
    """

    def __init__(self, session: Session):
        self.session = session

    def register_contract(self, contract: dict):
        """
        Register a new contract in the database.
        """
        current_contract = (
            self.session.query(CurrentState).filter_by(id=contract["id"]).one()
        )
        current_contract.data = contract["data"]
        self.session.commit()

    def update_contract_state(
        self,
        contract_address: str,
        accepted_state: dict[str, str] | None = None,
        finalized_state: dict[str, str] | None = None,
    ):
        """
        Update the accepted and/or finalized state of the contract in the database.
        """
        contract = (
            self.session.query(CurrentState)
            .filter_by(id=contract_address)
            .one_or_none()
        )

        if contract:
            new_state = {
                "accepted": (
                    accepted_state
                    if accepted_state is not None
                    else contract.data["state"]["accepted"]
                ),
                "finalized": (
                    finalized_state
                    if finalized_state is not None
                    else contract.data["state"]["finalized"]
                ),
            }
            new_contract_data = {
                "state": new_state,
            }

            contract.data = new_contract_data
            self.session.commit()

    def reset_contract(self, contract_address: str) -> bool:
        """
        Reset a contract from the database.

        Args:
            contract_address: The address of the contract to reset

        Returns:
            True if the contract was reset, False if the contract did not exist
        """
        current_contract = (
            self.session.query(CurrentState)
            .filter_by(id=contract_address)
            .one_or_none()
        )

        if current_contract:
            current_contract.data = {}
            current_contract.balance = 0
            self.session.commit()
            return True
        else:
            return False
