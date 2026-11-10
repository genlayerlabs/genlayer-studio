"""sim_fundAccount must never expose an uncredited funding tx to workers."""

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from backend.database_handler.accounts_manager import AccountsManager
from backend.protocol_rpc import endpoints

ADDRESS = "0x" + "ab" * 20


def test_fund_account_publishes_tx_already_credited(
    engine: Engine, session: Session, monkeypatch
):
    seen_by_worker: list[tuple] = []
    original_credit = AccountsManager.credit_tx_value_once

    def credit_after_peeking(self, tx_hash, target_address, amount):
        with engine.connect() as other:
            seen_by_worker.extend(
                other.execute(
                    text("SELECT value_credited FROM transactions WHERE hash = :h"),
                    {"h": tx_hash},
                ).all()
            )
        return original_credit(self, tx_hash, target_address, amount)

    monkeypatch.setattr(AccountsManager, "credit_tx_value_once", credit_after_peeking)

    tx_hash = endpoints.fund_account(session, ADDRESS, 1000)

    assert seen_by_worker == []
    with engine.connect() as other:
        row = other.execute(
            text("SELECT value_credited FROM transactions WHERE hash = :h"),
            {"h": tx_hash},
        ).one()
    assert row.value_credited is True
    assert AccountsManager(session).get_account_balance(ADDRESS) == 1000
