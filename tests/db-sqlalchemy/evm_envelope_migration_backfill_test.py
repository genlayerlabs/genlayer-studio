"""Regression coverage for the legacy EVM-envelope ledger backfill."""

import os

from alembic import command
from sqlalchemy import Engine, text

from conftest import _alembic_config

PRE_ENVELOPE_LEDGER_REVISION = "d2e3f4a5b6c7"


def test_envelope_backfill_prefers_dated_duplicate_over_null_timestamp(
    migrated_engine: Engine,
) -> None:
    cfg = _alembic_config(os.environ["POSTGRES_URL"])
    sender = "0x" + "ab" * 20
    null_timestamp_hash = "0x" + "ff" * 32
    dated_hash = "0x" + "11" * 32

    try:
        command.downgrade(cfg, PRE_ENVELOPE_LEDGER_REVISION)
        with migrated_engine.connect() as conn:
            for tx_hash, from_address, created_at in (
                (null_timestamp_hash, sender.upper(), None),
                (dated_hash, sender, "2026-08-28T12:00:00+00:00"),
            ):
                conn.execute(
                    text(
                        """
                        INSERT INTO transactions (
                            hash, from_address, nonce, value, type,
                            leader_only, appealed, appeal_undetermined,
                            appeal_leader_timeout, appeal_validators_timeout,
                            created_at
                        ) VALUES (
                            :hash, :from_address, 7, 0, 2,
                            false, false, false, false, false,
                            :created_at
                        )
                        """
                    ),
                    {
                        "hash": tx_hash,
                        "from_address": from_address,
                        "created_at": created_at,
                    },
                )
            conn.commit()

        command.upgrade(cfg, "head")

        with migrated_engine.connect() as conn:
            envelope_hash = conn.execute(
                text(
                    """
                    SELECT hash
                    FROM evm_envelopes
                    WHERE from_address = lower(:sender) AND nonce = 7
                    """
                ),
                {"sender": sender},
            ).scalar_one()

        assert envelope_hash == dated_hash
    finally:
        command.upgrade(cfg, "head")
        with migrated_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE evm_envelopes, transactions CASCADE"))
            conn.commit()
