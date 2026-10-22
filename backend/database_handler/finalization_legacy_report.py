"""Read-only report of ACCEPTED rows that lack a saved finalization state.

Run before deploying transaction-local finalization to see which legacy rows
will finalize, and from which verified source, and which will stay ACCEPTED.
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
    session.rollback()
    return totals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "List ACCEPTED transactions without a saved finalization state and "
            "classify how each would finalize. Read-only."
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
