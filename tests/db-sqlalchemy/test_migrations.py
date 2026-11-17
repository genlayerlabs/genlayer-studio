"""Migration-level integrity tests.

The per-session `migrated_engine` fixture in conftest.py runs
`alembic upgrade head` against the test database. That on its own would
have caught the CONCURRENTLY-inside-transaction bug that slipped to prd
on 2026-04-16.

These tests add two explicit checks on top:
- single migration head (no accidental branches from merge conflicts)
- no schema drift between Base.metadata (ORM models) and the migrated schema
"""

import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

from backend.database_handler.models import Base
from conftest import _alembic_config


def test_migrations_have_single_head(migrated_engine: Engine):
    """Alembic migration chain must have exactly one head.

    Multiple heads appear when two branches both add a migration pointing at
    the same down_revision (typical merge-conflict outcome). Prd can't apply
    such a chain without a manual `alembic merge`.
    """
    cfg = _alembic_config(os.environ["POSTGRES_URL"])
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Migration chain has {len(heads)} heads: {heads}. "
        "Run `alembic merge` to reconcile."
    )


def _is_benign_drift(diff_entry) -> bool:
    """Filter out drift entries that are expected and safe.

    Migrations routinely add performance indexes that aren't declared on the
    ORM models (partial indexes for claim queries, hot-path indexes for the
    explorer API, etc.). Those are intentional, not drift.
    """
    if not isinstance(diff_entry, tuple):
        return False
    action = diff_entry[0]
    return action in ("add_index", "remove_index")


def test_no_schema_drift_between_orm_and_migrations(migrated_engine: Engine):
    """After running every migration, the database schema must match
    `Base.metadata` (the ORM models).

    If this fails, models.py has diverged from the migration chain. Either
    a new migration was missed, or the models were edited without a
    corresponding migration. Both cases would quietly ship a broken schema
    to prd — this test catches it in CI.
    """
    with migrated_engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        diff = compare_metadata(mc, Base.metadata)

    significant = [d for d in diff if not _is_benign_drift(d)]
    if significant:
        pytest.fail(
            "Schema drift between models.py and migrations:\n  - "
            + "\n  - ".join(str(d) for d in significant)
            + "\n\nEither generate a new migration (`alembic revision "
            "--autogenerate`) or update models.py to match the intended "
            "schema."
        )


PRE_REROUTE_REVISION = "d0e1f2a3b4c5"


def test_backfill_migrations_lift_inherited_statement_timeout(
    migrated_engine: Engine,
):
    """e4f6a8c0d2b4 and f5a7c9e1b3d5 must not inherit c1d2e3f4a5b6's 30s limit.

    Probes record the statement_timeout in effect, since a timing-based repro
    would need prod-sized data.
    """
    cfg = _alembic_config(os.environ["POSTGRES_URL"])
    sender = "0x" + "cd" * 20
    hashes = ["0x" + f"{nonce:064x}" for nonce in range(5)]

    try:
        command.downgrade(cfg, PRE_REROUTE_REVISION)
        with migrated_engine.connect() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE migration_timeout_probe (
                    tag text NOT NULL,
                    object text,
                    statement_timeout text NOT NULL
                )
                """
            )
            conn.exec_driver_sql(
                """
                CREATE FUNCTION migration_timeout_probe_row() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    INSERT INTO migration_timeout_probe
                    VALUES (TG_OP, NEW.hash, current_setting('statement_timeout'));
                    RETURN NEW;
                END $$
                """
            )
            conn.exec_driver_sql(
                """
                CREATE TRIGGER migration_timeout_probe_row
                BEFORE UPDATE ON transactions
                FOR EACH ROW EXECUTE FUNCTION migration_timeout_probe_row()
                """
            )
            conn.exec_driver_sql(
                """
                CREATE FUNCTION migration_timeout_probe_ddl() RETURNS event_trigger
                LANGUAGE plpgsql AS $$
                DECLARE cmd record;
                BEGIN
                    FOR cmd IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
                        INSERT INTO migration_timeout_probe
                        VALUES (
                            cmd.command_tag,
                            cmd.object_identity,
                            current_setting('statement_timeout')
                        );
                    END LOOP;
                END $$
                """
            )
            conn.exec_driver_sql(
                """
                CREATE EVENT TRIGGER migration_timeout_probe_ddl
                ON ddl_command_end
                EXECUTE FUNCTION migration_timeout_probe_ddl()
                """
            )
            for nonce, tx_hash in enumerate(hashes):
                conn.execute(
                    text(
                        """
                        INSERT INTO transactions (
                            hash, from_address, nonce, value, type,
                            leader_only, appealed, appeal_undetermined,
                            appeal_leader_timeout, appeal_validators_timeout,
                            created_at
                        ) VALUES (
                            :hash, :from_address, :nonce, 0, 2,
                            false, false, false, false, false,
                            now()
                        )
                        """
                    ),
                    {
                        "hash": tx_hash,
                        "from_address": sender,
                        "nonce": nonce,
                    },
                )
            conn.commit()

        command.upgrade(cfg, "head")

        with migrated_engine.connect() as conn:
            probes = conn.execute(
                text(
                    "SELECT tag, object, statement_timeout FROM migration_timeout_probe"
                )
            ).all()
            envelopes = conn.execute(
                text("SELECT count(*) FROM evm_envelopes WHERE from_address = :s"),
                {"s": sender},
            ).scalar_one()
            unordered = conn.execute(
                text("SELECT count(*) FROM transactions WHERE queue_order IS NULL")
            ).scalar_one()

        def timeouts(tag: str, obj: str) -> set[str]:
            return {t for g, o, t in probes if g == tag and o == obj}

        # Precondition: the earlier revision really leaves 30s in force.
        assert timeouts("ALTER TABLE", "public.current_state") == {"30s"}
        assert timeouts("CREATE TABLE", "public.evm_envelopes") == {"0"}
        assert timeouts("CREATE SEQUENCE", "public.transactions_queue_order_seq") == {
            "0"
        }
        assert timeouts(
            "CREATE INDEX", "public.ix_transactions_to_address_queue_order"
        ) == {"0"}
        assert {o: t for g, o, t in probes if g == "UPDATE" and o in hashes} == {
            h: "0" for h in hashes
        }
        assert envelopes == len(hashes)
        assert unordered == 0
    finally:
        with migrated_engine.connect() as conn:
            conn.exec_driver_sql(
                "DROP EVENT TRIGGER IF EXISTS migration_timeout_probe_ddl"
            )
            conn.exec_driver_sql(
                "DROP TRIGGER IF EXISTS migration_timeout_probe_row ON transactions"
            )
            conn.exec_driver_sql(
                "DROP FUNCTION IF EXISTS migration_timeout_probe_ddl()"
            )
            conn.exec_driver_sql(
                "DROP FUNCTION IF EXISTS migration_timeout_probe_row()"
            )
            conn.exec_driver_sql("DROP TABLE IF EXISTS migration_timeout_probe")
            conn.commit()
        command.upgrade(cfg, "head")
        with migrated_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE evm_envelopes, transactions CASCADE"))
            conn.commit()
