"""Migration `c1d2e3f4a5b6` backfills the legacy executor selector for
pre-existing contracts (later renamed to `genvm_executor_selector` by
`d2e3f4a5b6c7`).

Studios being upgraded from before multi-version GenVM support only ever had
a single line (v0.2.x) to deploy against. A plain `ADD COLUMN reroute_to`
would leave every already-deployed contract unpinned, which means "resolve
from the manifest" (the current/latest line) -- silently moving contracts
onto an executor they were never deployed or tested against. The migration
must instead backfill a legacy selector onto genuine contract rows, while
leaving EOAs (and any row that already carries an explicit pin) untouched.
"""

import asyncio
import os

from alembic import command
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

import backend.node.genvm.origin.calldata as gvm_calldata
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.node.genvm.base import Host, is_valid_executor_selector
from backend.node.genvm.origin.public_abi import StorageType
from backend.node.types import Address

from conftest import _alembic_config

PRE_REROUTE_REVISION = "d0e1f2a3b4c5"
LEGACY_EXECUTOR_SELECTOR = r"re:^v0\.2\."


class _SingleContractStateProxy:
    """Minimal `StateProxy` stand-in: answers `genvm_executor_selector_for`
    for one address, the way `Host.resolve_callcontract_executor` reads it
    mid-run."""

    def __init__(self, address: str, genvm_executor_selector: str | None):
        self._address = address.lower()
        self._genvm_executor_selector = genvm_executor_selector

    def genvm_executor_selector_for(self, addr: Address) -> str | None:
        if addr.as_hex.lower() != self._address:
            return None
        return self._genvm_executor_selector


def test_migration_backfills_legacy_selector_for_pre_existing_contracts(
    migrated_engine: Engine,
):
    cfg = _alembic_config(os.environ["POSTGRES_URL"])
    contract_address = "0x" + "11" * 20
    pinned_contract_address = "0x" + "22" * 20
    eoa_address = "0x" + "33" * 20

    try:
        # Roll back to just before the column existed at all, so `head` has to
        # both add it and rename it -- matching a real Studio's pre-upgrade
        # schema all the way through both migrations.
        command.downgrade(cfg, PRE_REROUTE_REVISION)

        with migrated_engine.connect() as conn:
            # A genuine deployed contract: `data->'state'` present.
            conn.execute(
                text(
                    "INSERT INTO current_state (id, data, balance) "
                    "VALUES (:id, CAST(:data AS jsonb), 0)"
                ),
                {
                    "id": contract_address,
                    "data": '{"state": {"accepted": {}, "finalized": {}}}',
                },
            )
            # An EOA: `data = '{}'`, must not be touched by the backfill.
            conn.execute(
                text(
                    "INSERT INTO current_state (id, data, balance) "
                    "VALUES (:id, CAST(:data AS jsonb), 0)"
                ),
                {"id": eoa_address, "data": "{}"},
            )
            conn.commit()

        command.upgrade(cfg, "head")

        # A contract that already had an explicit pin set some other way,
        # after the column exists: the backfill must not clobber it.
        with migrated_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO current_state "
                    "(id, data, balance, genvm_executor_selector) "
                    "VALUES (:id, CAST(:data AS jsonb), 0, :selector)"
                ),
                {
                    "id": pinned_contract_address,
                    "data": '{"state": {"accepted": {}, "finalized": {}}}',
                    "selector": "v0.9.0",
                },
            )
            conn.commit()

        with migrated_engine.connect() as conn:
            rows = dict(
                conn.execute(
                    text(
                        "SELECT id, genvm_executor_selector FROM current_state "
                        "WHERE id IN (:c, :e, :p)"
                    ),
                    {
                        "c": contract_address,
                        "e": eoa_address,
                        "p": pinned_contract_address,
                    },
                ).all()
            )

        assert rows[contract_address] == LEGACY_EXECUTOR_SELECTOR
        assert rows[eoa_address] is None
        assert rows[pinned_contract_address] == "v0.9.0"

        # The backfilled value must be usable end-to-end for a cross-version
        # call, not merely present in the column: it has to pass the same
        # selector grammar submit-time validation enforces, load back through
        # `ContractSnapshot`, and let `resolve_callcontract_executor` answer a
        # caller on another line with a routable selector instead of raising.
        assert is_valid_executor_selector(LEGACY_EXECUTOR_SELECTOR)

        with Session(migrated_engine) as session:
            snapshot = ContractSnapshot(contract_address, session)
        assert snapshot.genvm_executor_selector == LEGACY_EXECUTOR_SELECTOR

        host = Host.__new__(Host)
        host._state_proxy = _SingleContractStateProxy(
            contract_address, snapshot.genvm_executor_selector
        )
        resolved = asyncio.run(
            host.resolve_callcontract_executor(
                Address(contract_address), StorageType.DEFAULT, 6
            )
        )
        assert gvm_calldata.decode(resolved) == {
            "kind": "version",
            "version": LEGACY_EXECUTOR_SELECTOR,
        }
    finally:
        with migrated_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE current_state RESTART IDENTITY CASCADE"))
            conn.commit()
