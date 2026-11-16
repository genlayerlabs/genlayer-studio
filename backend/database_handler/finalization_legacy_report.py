"""Read-only report of ACCEPTED rows that lack a saved finalization state.

Run before deploying transaction-local finalization to see which legacy rows
will finalize, and from which verified source, and which will stay ACCEPTED.
It also lists decided rows that a pre-change code upgrade overtook; they now
finalize on top of that upgrade's code instead of reverting it.
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from types import SimpleNamespace
from typing import Callable

from eth_utils import to_checksum_address
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.consensus.base import resolve_finalization_state
from backend.consensus.errors import FinalizationStateError
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.errors import ContractNotFoundError
from backend.database_handler.prune_terminal_snapshots import get_database_url
from backend.database_handler.transactions_processor import TransactionsProcessor


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

UNRECOVERABLE = "UNRECOVERABLE"

_LEGACY_ROWS = text(
    """
    SELECT hash, to_address, queue_order,
        consensus_data->'leader_receipt'->0->>'contract_state_hash'
            AS contract_state_hash
    FROM transactions
    WHERE status = 'ACCEPTED'
        AND consensus_data->'leader_receipt'->0->>'execution_result' = 'SUCCESS'
        AND COALESCE(
            jsonb_typeof(contract_snapshot->'finalization_state'), 'null'
        ) = 'null'
        AND COALESCE(
            consensus_data->'leader_receipt'->0->'contract_state', 'null'::jsonb
        ) IN ('null'::jsonb, '{}'::jsonb)
    ORDER BY queue_order ASC
    LIMIT :limit
    """
)

_UPGRADED_AFTER = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM transactions upgrade
        WHERE upgrade.type = 3
            AND upgrade.status = 'FINALIZED'
            AND upgrade.to_address = :to_address
            AND upgrade.queue_order > :queue_order
    )
    """
)

_INTERLEAVED_ROWS = text(
    """
    SELECT tx.hash, tx.to_address, tx.status, upgrade.hash AS upgrade_hash
    FROM transactions tx
    JOIN LATERAL (
        SELECT hash FROM transactions
        WHERE type = 3
            AND status = 'FINALIZED'
            AND to_address = tx.to_address
            AND queue_order > tx.queue_order
            AND NOT COALESCE(consensus_data ? 'applied_at', false)
        ORDER BY queue_order DESC
        LIMIT 1
    ) upgrade ON true
    WHERE tx.status IN (
        'ACCEPTED', 'UNDETERMINED', 'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT'
    )
    ORDER BY tx.queue_order ASC
    LIMIT :limit
    """
)

INTERLEAVED = "interleaved upgrade"


def interleaved_rows(session: Session, limit: int) -> list[dict]:
    """Decided rows older than a FINALIZED upgrade that did not close them first."""
    return [
        dict(row)
        for row in session.execute(_INTERLEAVED_ROWS, {"limit": limit}).mappings()
    ]


def classify(
    row: dict,
    transactions_processor,
    contract_snapshot_factory: Callable,
    upgraded_after: Callable[[dict], bool],
) -> tuple[str, str]:
    """Return (source, reason) the finalizer would use for a legacy row."""
    expected_hash = row.get("contract_state_hash")
    if not expected_hash:
        return UNRECOVERABLE, "no hash"

    context = SimpleNamespace(
        transaction=SimpleNamespace(
            hash=row["hash"], to_address=row["to_address"], contract_snapshot=None
        ),
        transactions_processor=transactions_processor,
        contract_snapshot_factory=contract_snapshot_factory,
    )
    receipt = SimpleNamespace(contract_state=None, contract_state_hash=expected_hash)
    try:
        _, source = resolve_finalization_state(context, receipt)
    except ContractNotFoundError:
        return UNRECOVERABLE, "contract not found"
    except FinalizationStateError:
        if upgraded_after(row):
            return UNRECOVERABLE, "upgrade after"
        return UNRECOVERABLE, "no matching snapshot"
    return source, ""


def report(session: Session, limit: int) -> Counter:
    session.execute(text("SET TRANSACTION READ ONLY"))
    transactions_processor = TransactionsProcessor(session)

    def upgraded_after(row: dict) -> bool:
        return bool(
            session.execute(
                _UPGRADED_AFTER,
                {"to_address": row["to_address"], "queue_order": row["queue_order"]},
            ).scalar_one()
        )

    totals: Counter = Counter()
    rows = session.execute(_LEGACY_ROWS, {"limit": limit}).mappings().all()
    for row in rows:
        source, reason = classify(
            dict(row),
            transactions_processor,
            lambda address: ContractSnapshot(to_checksum_address(address), session),
            upgraded_after,
        )
        totals[source.split(" (")[0]] += 1
        print(f"{row['hash']}\t{row['to_address']}\t{source}\t{reason}".rstrip())
    for row in interleaved_rows(session, limit):
        totals[INTERLEAVED] += 1
        print(
            f"{row['hash']}\t{row['to_address']}\t"
            f"{INTERLEAVED} ({row['upgrade_hash']})\t{row['status']}"
        )
    session.rollback()
    return totals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "List ACCEPTED transactions without a saved finalization state and "
            "classify how each would finalize, plus decided transactions that an "
            "earlier code upgrade overtook. Read-only."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="Maximum number of rows to classify, oldest first.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    engine = create_engine(get_database_url(), pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with SessionLocal() as session:
        totals = report(session, args.limit)
    logger.info("Legacy finalization report: %s", dict(totals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
