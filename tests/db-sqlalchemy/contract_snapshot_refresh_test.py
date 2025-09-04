from sqlalchemy.orm import Session

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.models import CurrentState


def test_contract_code_refreshed_between_snapshots(session: Session):
    """Test that contract code is refreshed from database for each new snapshot."""
    # Create initial contract with code
    contract_address = "0xtest123"
    initial_code = "initial_code"
    contract_state = {
        "accepted": {"key1": "value1"},
        "finalized": {},
    }

    # Add initial contract to database
    contract = CurrentState(
        id=contract_address, data={"code": initial_code, "state": contract_state}
    )
    session.add(contract)
    session.commit()

    # Create first snapshot - should load initial code
    snapshot1 = ContractSnapshot(contract_address, session)
    assert snapshot1.contract_code == initial_code
    assert snapshot1.states == contract_state

    # Update contract code in database directly
    updated_code = "updated_code"
    updated_state = {
        "accepted": {"key2": "value2"},
        "finalized": {"key3": "value3"},
    }

    # Update the contract in database
    contract_to_update = (
        session.query(CurrentState).filter_by(id=contract_address).one()
    )
    contract_to_update.data = {"code": updated_code, "state": updated_state}
    session.commit()

    # Create second snapshot - should load updated code due to our fix
    snapshot2 = ContractSnapshot(contract_address, session)
    assert snapshot2.contract_code == updated_code
    assert snapshot2.states == updated_state

    # Verify first snapshot remains unchanged (immutable)
    assert snapshot1.contract_code == initial_code
    assert snapshot1.states == contract_state


def test_contract_code_consistent_within_transaction(session: Session):
    """Test that contract snapshots are fresh when created with new sessions."""
    # Create initial contract
    contract_address = "0xtest456"
    initial_code = "code_v1"
    contract_state = {
        "accepted": {"data": "v1"},
        "finalized": {},
    }

    contract = CurrentState(
        id=contract_address, data={"code": initial_code, "state": contract_state}
    )
    session.add(contract)
    session.commit()

    # Create first snapshot
    snapshot1 = ContractSnapshot(contract_address, session)
    assert snapshot1.contract_code == initial_code

    # Update contract in database
    contract_to_update = (
        session.query(CurrentState).filter_by(id=contract_address).one()
    )
    contract_to_update.data = {
        "code": "code_v2",
        "state": {"accepted": {"data": "v2"}, "finalized": {}},
    }
    session.commit()

    # Create second snapshot in same session - will see fresh data due to expire_all
    snapshot2 = ContractSnapshot(contract_address, session)
    assert snapshot2.contract_code == "code_v2"

    # First snapshot should remain unchanged (immutable)
    assert snapshot1.contract_code == initial_code
