#!/usr/bin/env python3
"""
Dump historic transactions to the usage metrics service.

This script connects to the PostgreSQL database and sends all finalized
transactions to the external usage metrics API, from the first transaction
up to (but excluding) a given transaction hash.

Usage (from k8s pod):
    python dump_transactions_to_metrics.py \
        --db-host 10.24.72.12 \
        --db-password "$DBPASSWORD" \
        --api-url "https://your-metrics-api.com" \
        --api-key "your-api-key" \
        --until-hash "0xabc123..."

    # Dry run (no API calls):
    python dump_transactions_to_metrics.py \
        --db-host 10.24.72.12 \
        --db-password "$DBPASSWORD" \
        --dry-run \
        --until-hash "0xabc123..."

    # Process all finalized transactions (no hash limit):
    python dump_transactions_to_metrics.py \
        --db-host 10.24.72.12 \
        --db-password "$DBPASSWORD" \
        --api-url "https://your-metrics-api.com" \
        --api-key "your-api-key"
"""

import argparse
import json
import sys
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras
import requests


# Transaction type mapping (from backend/domain/types.py)
TRANSACTION_TYPE_MAP = {
    0: "deploy",
    1: "write",
    2: "write",  # SEND type treated as write
    3: "upgrade",
}

# Transaction status mapping
TRANSACTION_STATUS_MAP = {
    "ACCEPTED": "success",
    "FINALIZED": "success",
    "LEADER_TIMEOUT": "timeout",
    "VALIDATORS_TIMEOUT": "timeout",
    "UNDETERMINED": "undetermined",
}


def get_db_connection(
    host: str,
    password: str,
    port: int = 5432,
    user: str = "postgres",
    database: str = "genlayer_state",
):
    """Create database connection."""
    return psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )


def get_transaction_created_at(conn, tx_hash: str) -> Optional[datetime]:
    """Get the created_at timestamp of a transaction by hash."""
    with conn.cursor() as cur:
        cur.execute("SELECT created_at FROM transactions WHERE hash = %s", (tx_hash,))
        row = cur.fetchone()
        if row:
            return row[0]
    return None


def fetch_finalized_transactions(
    conn,
    from_created_at: Optional[datetime] = None,
    until_created_at: Optional[datetime] = None,
    batch_size: int = 100,
):
    """
    Fetch finalized transactions in batches.

    Yields batches of transaction dicts.
    """
    query = """
        SELECT
            hash,
            from_address,
            to_address,
            type,
            status,
            created_at,
            timestamp_awaiting_finalization,
            appeal_processing_time,
            consensus_data
        FROM transactions
        WHERE status = 'FINALIZED'
    """
    params = []

    if from_created_at:
        query += " AND created_at > %s"
        params.append(from_created_at)

    if until_created_at:
        query += " AND created_at < %s"
        params.append(until_created_at)

    query += " ORDER BY created_at ASC"

    # Use a named cursor for server-side cursor (avoids loading all rows into memory)
    with conn.cursor(
        name="tx_cursor", cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.itersize = batch_size  # Fetch this many rows at a time from server
        cur.execute(query, params)

        batch = []
        for row in cur:
            batch.append(dict(row))
            if len(batch) >= batch_size:
                yield batch
                batch = []

        if batch:
            yield batch


def calculate_processing_time_ms(tx: dict) -> int:
    """Calculate processing time in milliseconds."""
    timestamp_awaiting = tx.get("timestamp_awaiting_finalization")
    created_at = tx.get("created_at")
    appeal_processing_time = tx.get("appeal_processing_time") or 0

    if timestamp_awaiting is None or created_at is None:
        return 0

    try:
        if isinstance(created_at, datetime):
            created_at_epoch = created_at.timestamp()
        else:
            dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            created_at_epoch = dt.timestamp()

        processing_seconds = float(timestamp_awaiting) - created_at_epoch
        total_ms = int(processing_seconds * 1000) + int(appeal_processing_time * 1000)
        return max(0, total_ms)
    except Exception as e:
        print(f"Warning: Failed to calculate processing time for {tx['hash']}: {e}")
        return 0


def extract_execution_result(tx: dict) -> str:
    """
    Extract execution result from status and consensus_data.

    Returns one of: "success", "error", "timeout", "undetermined"

    Priority:
    1. Original status timeout (LEADER_TIMEOUT, VALIDATORS_TIMEOUT) -> "timeout"
    2. Original status undetermined (UNDETERMINED) -> "undetermined"
    3. execution_result from consensus_data.leader_receipt[0] -> "success" or "error"
    4. Default -> "success"
    """
    # Check original status for timeout/undetermined
    original_status = tx.get("status")
    if original_status in ("LEADER_TIMEOUT", "VALIDATORS_TIMEOUT"):
        return "timeout"
    if original_status == "UNDETERMINED":
        return "undetermined"

    # Try to get execution_result from leader receipt in consensus_data
    consensus_data = tx.get("consensus_data")
    if consensus_data is not None:
        leader_receipts = consensus_data.get("leader_receipt", [])
        if leader_receipts and len(leader_receipts) > 0:
            first_receipt = leader_receipts[0]
            if first_receipt is not None:
                execution_result = first_receipt.get("execution_result")
                if execution_result is not None:
                    # Handle string value
                    return str(execution_result).lower()

    # Default to success
    return "success"


def extract_llm_calls(consensus_data: Optional[dict]) -> list:
    """Extract LLM provider/model info from consensus_data."""
    llm_calls = []

    if consensus_data is None:
        return llm_calls

    def extract_from_receipt(receipt):
        if receipt is None:
            return None

        node_config = receipt.get("node_config") if isinstance(receipt, dict) else None
        if node_config is None or not isinstance(node_config, dict):
            return None

        primary_model = node_config.get("primary_model", {})
        if not primary_model:
            return None

        provider = primary_model.get("provider", "unknown")
        model = primary_model.get("model", "unknown")

        if provider == "unknown" and model == "unknown":
            return None

        return {
            "provider": provider,
            "model": model,
            "inputTokens": 0,
            "outputTokens": 0,
            "costUsd": 0,
        }

    # Process leader receipts
    leader_receipts = consensus_data.get("leader_receipt", [])
    if leader_receipts:
        for receipt in leader_receipts:
            llm_call = extract_from_receipt(receipt)
            if llm_call:
                llm_calls.append(llm_call)

    # Process validator receipts
    validators = consensus_data.get("validators", [])
    if validators:
        for receipt in validators:
            llm_call = extract_from_receipt(receipt)
            if llm_call:
                llm_calls.append(llm_call)

    return llm_calls


def build_decision_payload(tx: dict) -> dict:
    """Build the decision payload matching UsageMetricsService format."""
    tx_type = TRANSACTION_TYPE_MAP.get(tx.get("type"), "write")
    tx_status = TRANSACTION_STATUS_MAP.get(tx.get("status"), "undetermined")
    processing_time_ms = calculate_processing_time_ms(tx)
    llm_calls = extract_llm_calls(tx.get("consensus_data"))
    execution_result = extract_execution_result(tx)

    created_at = tx.get("created_at")
    if isinstance(created_at, datetime):
        created_at_iso = created_at.isoformat()
    else:
        created_at_iso = (
            str(created_at) if created_at else datetime.utcnow().isoformat()
        )

    return {
        "externalId": tx["hash"],
        "walletAddress": tx.get("from_address")
        or "0x0000000000000000000000000000000000000000",
        "contractAddress": tx.get("to_address"),
        "type": tx_type,
        "status": tx_status,
        "processingTimeMs": processing_time_ms,
        "createdAt": created_at_iso,
        "llmCalls": llm_calls,
        "result": execution_result,
    }


def send_to_api(api_url: str, api_key: str, decisions: list) -> bool:
    """Send decisions batch to the API."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {"decisions": decisions}

    try:
        response = requests.post(
            f"{api_url}/api/ingest",
            json=payload,
            headers=headers,
            timeout=30,
        )

        if response.status_code != 200:
            print(
                f"Warning: API returned status {response.status_code}: {response.text[:200]}"
            )
            return False

        return True
    except requests.Timeout:
        print("Warning: API request timed out")
        return False
    except Exception as e:
        print(f"Error sending to API: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Dump historic transactions to usage metrics service"
    )
    parser.add_argument("--db-host", required=True, help="Database host IP")
    parser.add_argument("--db-port", type=int, default=5432, help="Database port")
    parser.add_argument("--db-user", default="postgres", help="Database user")
    parser.add_argument("--db-password", required=True, help="Database password")
    parser.add_argument("--db-name", default="genlayer_state", help="Database name")
    parser.add_argument(
        "--api-url", help="Usage metrics API URL (required unless --dry-run)"
    )
    parser.add_argument(
        "--api-key", help="Usage metrics API key (required unless --dry-run)"
    )
    parser.add_argument(
        "--from-hash", help="Process transactions after this hash (exclusive)"
    )
    parser.add_argument(
        "--until-hash", help="Process transactions before this hash (exclusive)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=50, help="Batch size for API calls"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print payloads without sending"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if not args.dry_run and (not args.api_url or not args.api_key):
        parser.error(
            "--api-url and --api-key are required unless --dry-run is specified"
        )

    # Connect to database
    print(f"Connecting to database at {args.db_host}:{args.db_port}...")
    conn = get_db_connection(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        database=args.db_name,
    )

    # Get from_created_at if hash provided
    from_created_at = None
    if args.from_hash:
        from_created_at = get_transaction_created_at(conn, args.from_hash)
        if from_created_at is None:
            print(f"Error: Transaction with hash {args.from_hash} not found")
            sys.exit(1)
        print(
            f"Processing transactions after {from_created_at} (excluding hash: {args.from_hash})"
        )

    # Get until_created_at if hash provided
    until_created_at = None
    if args.until_hash:
        until_created_at = get_transaction_created_at(conn, args.until_hash)
        if until_created_at is None:
            print(f"Error: Transaction with hash {args.until_hash} not found")
            sys.exit(1)
        print(
            f"Processing transactions before {until_created_at} (excluding hash: {args.until_hash})"
        )

    if not args.from_hash and not args.until_hash:
        print("Processing all finalized transactions")

    # Process transactions in batches
    total_processed = 0
    total_sent = 0
    total_failed = 0

    for batch in fetch_finalized_transactions(
        conn, from_created_at, until_created_at, batch_size=args.batch_size
    ):
        decisions = [build_decision_payload(tx) for tx in batch]
        total_processed += len(decisions)

        if args.dry_run:
            if args.verbose:
                for decision in decisions:
                    print(json.dumps(decision, indent=2, default=str))
            print(
                f"[DRY RUN] Would send {len(decisions)} decisions (total: {total_processed})"
            )
        else:
            success = send_to_api(args.api_url, args.api_key, decisions)
            if success:
                total_sent += len(decisions)
                print(f"Sent {len(decisions)} decisions (total sent: {total_sent})")
            else:
                total_failed += len(decisions)
                print(
                    f"Failed to send {len(decisions)} decisions (total failed: {total_failed})"
                )

    conn.close()

    # Summary
    print("\n" + "=" * 50)
    print("Summary:")
    print(f"  Total processed: {total_processed}")
    if args.dry_run:
        print("  Mode: DRY RUN (no API calls made)")
    else:
        print(f"  Successfully sent: {total_sent}")
        print(f"  Failed: {total_failed}")


if __name__ == "__main__":
    main()
