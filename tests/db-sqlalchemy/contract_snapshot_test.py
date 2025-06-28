from sqlalchemy.orm import Session

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.models import CurrentState
from backend.database_handler.contract_processor import ContractProcessor


def test_contract_snapshot_with_contract(session: Session):
    # Pre-load contract
    contract_address = "0x123456"
    contract_code = "code"
    contract_state = {
        "accepted": {
            "aaa": "bbb",
            "ccc": "ddd",
        },
        "finalized": {
            "aaa": "bbb",
            "ccc": "ddd",
        },
    }
    contract = CurrentState(
        id=contract_address, data={"code": contract_code, "state": contract_state}
    )

    session.add(contract)
    session.commit()

    # Test ContractSnapshot
    contract_snapshot = ContractSnapshot(contract_address, session)
    contract_processor = ContractProcessor(session)

    assert contract_snapshot.contract_address == contract_address
    assert contract_snapshot.contract_code == contract_code

    assert contract_snapshot.states == contract_state

    new_state = {
        "accepted": {},
        "finalized": {
            "aaa": "bbb",
            "ccc": "ddd",
        },
    }
    contract_processor.update_contract_state(
        contract_address,
        accepted_state=new_state["accepted"],
        finalized_state=new_state["finalized"],
    )

    actual_contract = session.query(CurrentState).filter_by(id=contract_address).one()

    assert actual_contract.data["state"] == new_state
    assert actual_contract.data["code"] == contract_code

    new_state = {
        "accepted": {
            "aaa": "bbb",
            "ccc": "ddd",
        },
        "finalized": {},
    }
    contract_processor.update_contract_state(
        contract_address,
        accepted_state=new_state["accepted"],
        finalized_state=new_state["finalized"],
    )

    actual_contract = session.query(CurrentState).filter_by(id=contract_address).one()

    assert actual_contract.data["state"] == new_state
    assert actual_contract.data["code"] == contract_code


def test_contract_snapshot_without_contract(session: Session):
    contract_address = "0x123456"
    contract_code = "code"
    contract_state = "state"
    contract = CurrentState(
        id=contract_address, data={"code": contract_code, "state": contract_state}
    )
    session.add(contract)

    contract_snapshot = ContractSnapshot(None, session)
    contract_processor = ContractProcessor(session)

    assert "contract_address" not in contract_snapshot.__dict__
    assert "contract_data" not in contract_snapshot.__dict__
    assert "contract_code" not in contract_snapshot.__dict__
    assert "states" not in contract_snapshot.__dict__

    updated_data = {
        "code": "new_code",
        "state": {"accepted": "new_acc_state", "finalized": "new_fin_state"},
    }
    updated_contract = {"id": contract_address, "data": updated_data}
    contract_processor.register_contract(updated_contract)

    actual_contract = session.query(CurrentState).filter_by(id=contract.id).one()

    assert actual_contract.data == updated_data
    assert actual_contract.id == contract_address
