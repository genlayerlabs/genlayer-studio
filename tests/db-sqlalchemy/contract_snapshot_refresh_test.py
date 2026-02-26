from sqlalchemy.orm import Session

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.models import CurrentState


def test_contract_code_refreshed_between_snapshots(session: Session):
    """Test that contract state is refreshed from database for each new snapshot."""
    # Create initial contract
    contract_address = "0xtest123"
    contract_state = {
        "accepted": {"key1": "value1"},
        "finalized": {},
    }

    # Add initial contract to database
    contract = CurrentState(id=contract_address, data={"state": contract_state})
    session.add(contract)
    session.commit()

    # Create first snapshot - should load initial state
    snapshot1 = ContractSnapshot(contract_address, session)
    assert snapshot1.states == contract_state

    # Update contract state in database directly
    updated_state = {
        "accepted": {"key2": "value2"},
        "finalized": {"key3": "value3"},
    }

    # Update the contract in database
    contract_to_update = (
        session.query(CurrentState).filter_by(id=contract_address).one()
    )
    contract_to_update.data = {"state": updated_state}
    session.commit()

    # Create second snapshot - should load updated state due to our fix
    snapshot2 = ContractSnapshot(contract_address, session)
    assert snapshot2.states == updated_state

    # Verify first snapshot remains unchanged (immutable)
    assert snapshot1.states == contract_state


def test_contract_state_consistent_within_transaction(session: Session):
    """Test that contract snapshots are fresh when created with new sessions."""
    # Create initial contract
    contract_address = "0xtest456"
    contract_state = {
        "accepted": {"data": "v1"},
        "finalized": {},
    }

    contract = CurrentState(id=contract_address, data={"state": contract_state})
    session.add(contract)
    session.commit()

    # Create first snapshot
    snapshot1 = ContractSnapshot(contract_address, session)
    assert snapshot1.states == contract_state

    # Update contract in database
    contract_to_update = (
        session.query(CurrentState).filter_by(id=contract_address).one()
    )
    contract_to_update.data = {
        "state": {"accepted": {"data": "v2"}, "finalized": {}},
    }
    session.commit()

    # Create second snapshot in same session - will see fresh data due to expire_all
    snapshot2 = ContractSnapshot(contract_address, session)
    assert snapshot2.states == {"accepted": {"data": "v2"}, "finalized": {}}

    # First snapshot should remain unchanged (immutable)
    assert snapshot1.states == contract_state
