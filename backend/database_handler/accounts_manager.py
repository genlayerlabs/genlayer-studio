# consensus/services/transactions_db_service.py

from eth_account import Account
from eth_utils import is_address, to_checksum_address

from .models import CurrentState, Transactions
from backend.database_handler.errors import AccountNotFoundError
from backend.protocol_rpc.fees import (
    FEE_ACCOUNTING_KEY,
    abort_latest_appeal_admission,
    cancel_fee_accounting,
    settle_fee_accounting,
)
from backend.consensus.history import (
    ACTIVE_APPEAL_BASIS_KEY,
    APPEAL_RECOVERY_SNAPSHOT_KEY,
    completed_consensus_round_index,
)

from sqlalchemy.orm import Session
from sqlalchemy import text


class AccountsManager:
    def __init__(self, session: Session):
        self.session = session

    def _parse_account_data(self, account_data: CurrentState) -> dict:
        return {
            "id": account_data.id,
            "data": account_data.data,
            "balance": account_data.balance,
            "updated_at": account_data.updated_at.isoformat(),
        }

    def create_new_account(self, *, commit: bool = True) -> Account:
        """
        Used when generating intelligent contract's accounts or sending funds to a new account.
        Users should create their accounts client-side
        """
        account = Account.create()
        self.create_new_account_with_address(account.address, commit=commit)
        return account

    def create_new_account_with_address(
        self,
        address: str,
        *,
        commit: bool = True,
    ) -> Account:
        # Check if account already exists
        if not is_address(address):
            raise ValueError(f"Invalid address: {address}")

        try:
            address = to_checksum_address(address)
        except Exception:
            pass

        existing_account = (
            self.session.query(CurrentState).filter(CurrentState.id == address).first()
        )
        if existing_account is not None:
            return existing_account

        # If account doesn't exist, create it
        account = CurrentState(id=address, data={}, balance=0)
        self.session.add(account)
        if commit:
            self.session.commit()
        else:
            self.session.flush()
        return account

    def is_valid_address(self, address: str) -> bool:
        return is_address(address)

    def get_account(self, account_address: str) -> CurrentState | None:
        """Private method to retrieve an account from the data base"""
        try:
            normalized = to_checksum_address(account_address)
        except Exception:
            normalized = account_address
        account = (
            self.session.query(CurrentState)
            .filter(CurrentState.id == normalized)
            .one_or_none()
        )
        return account

    def get_account_or_fail(self, account_address: str) -> dict:
        """Private method to check if an account exists, and raise an error if not."""
        account_data = self.get_account(account_address)
        if not account_data:
            raise AccountNotFoundError(
                account_address, f"Account {account_address} does not exist."
            )
        return self._parse_account_data(account_data)

    def get_account_balance(self, account_address: str) -> int:
        account = self.get_account(account_address)
        if not account:
            return 0
        return account.balance

    def update_account_balance(self, account_address: str, new_balance: int):
        to_account = self.get_account(account_address)
        if to_account is None:
            self.create_new_account_with_address(account_address)
            to_account = self.get_account(account_address)
        to_account.balance = new_balance

    def debit_account_balance(self, account_address: str, amount: int) -> bool:
        """Atomic conditional debit. Returns False if insufficient balance."""
        if amount <= 0:
            return True
        try:
            account_address = to_checksum_address(account_address)
        except Exception:
            pass
        result = self.session.execute(
            text(
                "UPDATE current_state SET balance = balance - :amount "
                "WHERE id = :addr AND balance >= :amount"
            ),
            {"amount": amount, "addr": account_address},
        )
        return result.rowcount > 0

    def credit_account_balance(self, account_address: str, amount: int):
        """Atomic credit. Creates account if it doesn't exist."""
        if amount <= 0:
            return
        try:
            account_address = to_checksum_address(account_address)
        except Exception:
            pass
        self.session.execute(
            text(
                "INSERT INTO current_state (id, data, balance) "
                "VALUES (:addr, '{}'::jsonb, 0) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"addr": account_address},
        )
        self.session.execute(
            text(
                "UPDATE current_state SET balance = balance + :amount "
                "WHERE id = :addr"
            ),
            {"amount": amount, "addr": account_address},
        )

    def credit_tx_value_once(
        self, tx_hash: str, target_address: str, amount: int
    ) -> bool:
        """Idempotent activation credit: credits target only if tx not already credited.
        Sets value_credited=true atomically. Returns True if credit was applied."""
        if amount <= 0:
            return False
        # Ensure target account exists
        self.session.execute(
            text(
                "INSERT INTO current_state (id, data, balance) "
                "VALUES (:addr, '{}'::jsonb, 0) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"addr": target_address},
        )
        # Atomic: set value_credited and credit balance in one operation
        result = self.session.execute(
            text(
                "UPDATE transactions SET value_credited = true "
                "WHERE hash = :hash AND value_credited = false AND value > 0"
            ),
            {"hash": tx_hash},
        )
        if result.rowcount > 0:
            self.session.execute(
                text(
                    "UPDATE current_state SET balance = balance + :amount "
                    "WHERE id = :addr"
                ),
                {"amount": amount, "addr": target_address},
            )
            # Expire ORM cache so subsequent reads see post-credit balances
            self.session.expire_all()
            return True
        return False

    def refund_tx_value(self, tx_hash: str, sender_address: str) -> bool:
        """Refund sender for a canceled/failed payable tx if value not yet credited to target.
        Returns True if refund was applied."""
        result = self.session.execute(
            text(
                "UPDATE transactions "
                "SET data = jsonb_set(COALESCE(data, '{}'::jsonb), "
                "'{uncreditedValueRefunded}', 'true'::jsonb, true) "
                "WHERE hash = :hash AND value_credited = false AND value > 0 "
                "AND COALESCE(data ->> 'uncreditedValueRefunded', 'false') <> 'true' "
                "RETURNING value"
            ),
            {"hash": tx_hash},
        )
        row = result.first()
        if row is None:
            return False
        self.credit_account_balance(sender_address, row.value)
        self.session.expire_all()
        return True

    def refund_activated_tx_value_once(
        self,
        tx_hash: str,
        target_address: str,
        beneficiary_address: str,
    ) -> bool:
        """Return an activated transaction's value to its principal owner once.

        Activation credits the destination before consensus has a terminal
        result.  A no-result/error finalization must reverse that provisional
        credit.  For an internal child the owner is ``from_address`` (the
        emitting contract), never the outer transaction origin.
        """

        result = self.session.execute(
            text(
                "UPDATE transactions "
                "SET data = jsonb_set(COALESCE(data, '{}'::jsonb), "
                "'{activatedValueRefunded}', 'true'::jsonb, true) "
                "WHERE hash = :hash AND value_credited = true AND value > 0 "
                "AND COALESCE(data ->> 'activatedValueRefunded', 'false') <> 'true' "
                "RETURNING value"
            ),
            {"hash": tx_hash},
        )
        row = result.first()
        if row is None:
            return False

        amount = int(row.value)
        if not self.debit_account_balance(target_address, amount):
            # Raising keeps the status update, marker, debit, and refund in one
            # database rollback domain.  A retry can therefore repair the
            # lifecycle instead of silently minting or misdirecting value.
            raise RuntimeError(
                f"ActivatedValueRefundInsufficientBalance({tx_hash},{target_address},{amount})"
            )
        self.credit_account_balance(beneficiary_address, amount)
        self.session.expire_all()
        return True

    def cancel_tx_fee_accounting_once(
        self, tx_hash: str, sender_address: str, reason: str = "canceled"
    ) -> int:
        transaction = (
            self.session.query(Transactions)
            .filter_by(hash=tx_hash)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if transaction is None:
            return 0
        if not isinstance(transaction.data, dict):
            return 0
        data = dict(transaction.data)
        accounting = data.get(FEE_ACCOUNTING_KEY)
        if not accounting:
            return 0
        sender_address = accounting.get("sender") or sender_address
        was_terminal = accounting.get("status") in {"settled", "canceled"}
        updated, refund = cancel_fee_accounting(accounting, reason=reason)
        data[FEE_ACCOUNTING_KEY] = updated
        data.pop(APPEAL_RECOVERY_SNAPSHOT_KEY, None)
        transaction.data = data
        if refund > 0:
            self._credit_fee_refund_settlements(updated, sender_address, refund)
        if not was_terminal:
            self._credit_external_message_fee_payouts(updated)
            self._credit_appeal_bond_payouts(updated)
        return refund

    def abort_tx_appeal_admission_once(
        self,
        tx_hash: str,
        reason: str = "appeal_committee_unavailable",
    ) -> int:
        """Atomically undo a paid appeal that never formed its committee."""

        transaction = (
            self.session.query(Transactions)
            .filter_by(hash=tx_hash)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if transaction is None or not isinstance(transaction.data, dict):
            return 0
        data = dict(transaction.data)
        accounting = data.get(FEE_ACCOUNTING_KEY)
        if not isinstance(accounting, dict):
            return 0
        updated, recipient, refund = abort_latest_appeal_admission(
            accounting,
            reason=reason,
        )
        if not recipient:
            return 0

        data[FEE_ACCOUNTING_KEY] = updated
        data.pop(APPEAL_RECOVERY_SNAPSHOT_KEY, None)
        transaction.data = data
        history = dict(transaction.consensus_history or {})
        history.pop(ACTIVE_APPEAL_BASIS_KEY, None)
        transaction.consensus_history = history
        transaction.appealed = False
        transaction.timestamp_appeal = None
        # Persist the ORM rollback before the raw-SQL credit and cache expiry;
        # expiring an unflushed row would resurrect the paid appeal and make a
        # retry refund it a second time.
        self.session.flush()
        if refund > 0:
            self.credit_account_balance(str(recipient), refund)
        self.session.expire_all()
        return refund

    def settle_tx_fee_accounting_once(
        self,
        tx_hash: str,
        sender_address: str,
        receipt=None,
        reason: str = "finalized",
    ) -> int:
        transaction = (
            self.session.query(Transactions)
            .filter_by(hash=tx_hash)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if transaction is None:
            return 0
        if not isinstance(transaction.data, dict):
            return 0
        data = dict(transaction.data)
        accounting = data.get(FEE_ACCOUNTING_KEY)
        if not accounting:
            return 0
        sender_address = accounting.get("sender") or sender_address
        was_terminal = accounting.get("status") in {"settled", "canceled"}
        updated, refund = settle_fee_accounting(
            accounting,
            receipt=receipt,
            reason=reason,
            actual_final_round=_infer_final_round(transaction.consensus_history),
            num_of_validators=transaction.num_of_initial_validators,
            consensus_history=transaction.consensus_history,
            execution_mode=(
                transaction.execution_mode.value
                if hasattr(transaction.execution_mode, "value")
                else str(transaction.execution_mode or "NORMAL")
            ),
        )
        data[FEE_ACCOUNTING_KEY] = updated
        data.pop(APPEAL_RECOVERY_SNAPSHOT_KEY, None)
        transaction.data = data
        if refund > 0:
            self._credit_fee_refund_settlements(updated, sender_address, refund)
        if not was_terminal:
            self._credit_external_message_fee_payouts(updated)
            self._credit_appeal_bond_payouts(updated)
        return refund

    def _credit_fee_refund_settlements(
        self,
        accounting: dict,
        fallback_recipient: str,
        total_refund: int,
    ) -> None:
        settlements = accounting.get("fee_refund_settlements") or []
        credited = 0
        for settlement in settlements:
            if not isinstance(settlement, dict):
                continue
            recipient = settlement.get("recipient")
            amount = int(settlement.get("amount", 0) or 0)
            if recipient and amount > 0:
                self.credit_account_balance(recipient, amount)
                credited += amount
        remainder = max(0, int(total_refund) - credited)
        if remainder > 0:
            self.credit_account_balance(
                accounting.get("sender") or fallback_recipient,
                remainder,
            )

    def _credit_appeal_bond_payouts(self, accounting: dict) -> None:
        for payout in accounting.get("appeal_bond_settlements") or []:
            if not isinstance(payout, dict):
                continue
            amount = int(payout.get("payout", 0) or 0)
            appealer = payout.get("appealer")
            if amount > 0 and appealer:
                self.credit_account_balance(appealer, amount)

    def _credit_external_message_fee_payouts(self, accounting: dict) -> None:
        for payout in accounting.get("external_message_fee_payouts") or []:
            if not isinstance(payout, dict):
                continue
            recipient = payout.get("recipient")
            amount = int(payout.get("amount", 0) or 0)
            if amount > 0 and recipient:
                self.credit_account_balance(recipient, amount)


def _infer_final_round(consensus_history: dict | None) -> int:
    return completed_consensus_round_index(consensus_history)
