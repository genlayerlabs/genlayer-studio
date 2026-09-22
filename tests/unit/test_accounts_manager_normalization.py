"""Unit tests for AccountsManager address normalization in raw-SQL paths.

debit_account_balance / credit_account_balance / credit_tx_value_once use
raw SQL and previously received the address verbatim, while ORM paths
(get_account / create_new_account_with_address) store rows keyed by
to_checksum_address(). A lowercase address therefore created a duplicate
account row and split balances. These tests exercise the fix with a mocked
SQLAlchemy session — no database required.
"""

from unittest.mock import MagicMock

from eth_utils import to_checksum_address

from backend.database_handler.accounts_manager import AccountsManager

RAW_ADDRESS = "0x71c7656ec7ab88b098defb751b7401b5f6d8976f"  # lowercase on purpose
CHECKSUMMED = to_checksum_address(RAW_ADDRESS)
assert RAW_ADDRESS != CHECKSUMMED  # fixture sanity: casing must differ


def _executed_params(session: MagicMock) -> list[dict]:
    return [call.args[1] for call in session.execute.call_args_list]


def test_debit_normalizes_address():
    session = MagicMock()
    session.execute.return_value = MagicMock(rowcount=1)
    assert AccountsManager(session).debit_account_balance(RAW_ADDRESS, 5) is True

    (params,) = [
        p for p in _executed_params(session) if p.get("addr")
    ]
    assert params["addr"] == CHECKSUMMED


def test_credit_normalizes_address_before_insert_and_update():
    session = MagicMock()
    AccountsManager(session).credit_account_balance(RAW_ADDRESS, 7)

    params = [p for p in _executed_params(session) if p.get("addr")]
    assert len(params) == 2  # INSERT ... ON CONFLICT + UPDATE
    assert all(p["addr"] == CHECKSUMMED for p in params)


def test_credit_tx_value_once_normalizes_target():
    session = MagicMock()
    # value_credited update must match so the credit path is taken
    session.execute.return_value = MagicMock(rowcount=1)

    manager = AccountsManager(session)
    trx = manager.credit_tx_value_once("0x12" + "34" * 16, RAW_ADDRESS, 3)

    assert trx is True
    params = [p for p in _executed_params(session) if p.get("addr")]
    assert params
    assert all(p["addr"] == CHECKSUMMED for p in params)


def test_credit_zero_or_negative_amount_is_noop():
    session = MagicMock()
    manager = AccountsManager(session)

    manager.credit_account_balance(RAW_ADDRESS, 0)
    manager.credit_account_balance(RAW_ADDRESS, -10)
    assert manager.debit_account_balance(RAW_ADDRESS, 0) is True

    session.execute.assert_not_called()


def test_normalize_fallback_keeps_original():
    """A value that to_checksum_address rejects must be used unchanged,
    preserving the pre-existing behavior for non-address identifiers."""
    session = MagicMock()
    session.execute.return_value = MagicMock(rowcount=1)
    AccountsManager(session).debit_account_balance("not-an-address", 5)

    (params,) = [p for p in _executed_params(session) if p.get("addr")]
    assert params["addr"] == "not-an-address"
