"""Test that Numeric column values are always plain int, never Decimal.

The Numeric(78,0) columns for balance and value can return decimal.Decimal
from PostgreSQL. GenVM's calldata encoder, JSON serializers, and snapshot
serialization all require plain int. This test ensures the IntNumeric
TypeDecorator and all conversion paths produce int.
"""

import decimal

from sqlalchemy.orm import Session

from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.models import CurrentState


WEI = 10**18


class TestNumericColumnsReturnInt:
    """Values read from Numeric columns must be int, not Decimal."""

    def test_balance_is_int_after_update(self, session: Session):
        am = AccountsManager(session)
        addr = "0x1000000000000000000000000000000000000001"
        am.create_new_account_with_address(addr)
        am.update_account_balance(addr, 500 * WEI)
        session.commit()
        session.expire_all()

        balance = am.get_account_balance(addr)
        assert isinstance(balance, int), f"Expected int, got {type(balance)}"
        assert balance == 500 * WEI

    def test_balance_is_int_after_credit(self, session: Session):
        am = AccountsManager(session)
        addr = "0x2000000000000000000000000000000000000001"
        am.create_new_account_with_address(addr)
        am.credit_account_balance(addr, 123 * WEI)
        session.commit()
        session.expire_all()

        balance = am.get_account_balance(addr)
        assert isinstance(balance, int), f"Expected int, got {type(balance)}"
        assert balance == 123 * WEI

    def test_balance_is_int_after_debit(self, session: Session):
        am = AccountsManager(session)
        addr = "0x3000000000000000000000000000000000000001"
        am.create_new_account_with_address(addr)
        am.update_account_balance(addr, 1000 * WEI)
        session.commit()

        am.debit_account_balance(addr, 300 * WEI)
        session.commit()
        session.expire_all()

        balance = am.get_account_balance(addr)
        assert isinstance(balance, int), f"Expected int, got {type(balance)}"
        assert balance == 700 * WEI

    def test_transaction_value_is_int(self, session: Session):
        tp = TransactionsProcessor(session)
        tx_hash = tp.insert_transaction(
            from_address="0x4000000000000000000000000000000000000001",
            to_address="0x5000000000000000000000000000000000000001",
            data={},
            value=456 * WEI,
            type=2,
            nonce=0,
            leader_only=False,
            config_rotation_rounds=3,
        )
        session.commit()
        session.expire_all()

        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx is not None
        value = tx["value"]
        assert isinstance(value, int), f"Expected int, got {type(value)}: {value}"
        assert value == 456 * WEI
        assert not isinstance(value, decimal.Decimal)

    def test_balance_not_decimal_from_raw_query(self, session: Session):
        """Raw SQL query should also return int thanks to IntNumeric."""
        am = AccountsManager(session)
        addr = "0x6000000000000000000000000000000000000001"
        am.create_new_account_with_address(addr)
        am.update_account_balance(addr, 789 * WEI)
        session.commit()
        session.expire_all()

        row = session.query(CurrentState).filter_by(id=addr).one()
        assert isinstance(row.balance, int), f"Expected int, got {type(row.balance)}"
        assert not isinstance(row.balance, decimal.Decimal)


class TestSnapshotBalanceIsInt:
    """ContractSnapshot serialization round-trips balance as int."""

    def test_to_dict_balance_is_int(self):
        snap = ContractSnapshot.from_dict(
            {"contract_address": "0xabc", "states": {"accepted": {}, "finalized": {}}}
        )
        snap.balance = 100 * WEI
        d = snap.to_dict()
        assert isinstance(d["balance"], int), f"Expected int, got {type(d['balance'])}"

    def test_from_dict_converts_decimal_to_int(self):
        """If balance was stored as Decimal (legacy), from_dict converts to int."""
        d = {
            "contract_address": "0xabc",
            "states": {"accepted": {}, "finalized": {}},
            "balance": decimal.Decimal("456000000000000000000"),
        }
        snap = ContractSnapshot.from_dict(d)
        assert isinstance(snap.balance, int), f"Expected int, got {type(snap.balance)}"
        assert snap.balance == 456 * WEI

    def test_from_dict_handles_none_balance(self):
        d = {
            "contract_address": "0xabc",
            "states": {"accepted": {}, "finalized": {}},
        }
        snap = ContractSnapshot.from_dict(d)
        assert snap.balance is None

    def test_round_trip_preserves_int(self):
        snap = ContractSnapshot.from_dict(
            {
                "contract_address": "0xabc",
                "states": {"accepted": {"k": "v"}, "finalized": {}},
                "balance": 999 * WEI,
            }
        )
        d = snap.to_dict()
        snap2 = ContractSnapshot.from_dict(d)
        assert isinstance(snap2.balance, int)
        assert snap2.balance == 999 * WEI
