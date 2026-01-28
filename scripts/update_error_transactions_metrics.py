#!/usr/bin/env python3
"""
Update error/timeout/undetermined transactions in the usage metrics service.

This script connects to the PostgreSQL database and sends transactions that have
result="error", "timeout", or "undetermined" to the external usage metrics API
to update their records with the new "result" field.

Optimized for large datasets (70k+ transactions) with:
- Chunked processing with server-side cursors
- Progress reporting with percentage and ETA
- Retry logic for failed API calls
- Real-time output flushing

Usage (from k8s pod):
    python update_error_transactions_metrics.py \
        --db-host 10.24.72.12 \
        --db-password "$DBPASSWORD" \
        --api-url "https://your-metrics-api.com" \
        --api-key "your-api-key"

    # Dry run (no API calls):
    python update_error_transactions_metrics.py \
        --db-host 10.24.72.12 \
        --db-password "$DBPASSWORD" \
        --dry-run

    # Process transactions after a specific hash (exclusive):
    python update_error_transactions_metrics.py \
        --db-host 10.24.72.12 \
        --db-password "$DBPASSWORD" \
        --api-url "https://your-metrics-api.com" \
        --api-key "your-api-key" \
        --from-hash "0xabc123..."

    # Process transactions before a specific hash (exclusive):
    python update_error_transactions_metrics.py \
        --db-host 10.24.72.12 \
        --db-password "$DBPASSWORD" \
        --api-url "https://your-metrics-api.com" \
        --api-key "your-api-key" \
        --until-hash "0xabc123..."
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import psycopg2
import psycopg2.extras
import requests


def log(message: str):
    """Print message with timestamp and flush immediately."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


# Transaction type mapping (from backend/domain/types.py)
TRANSACTION_TYPE_MAP = {
    0: "deploy",
    1: "write",
    2: "write",  # SEND type treated as write
    3: "upgrade",
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


def get_total_transaction_count(
    conn,
    from_created_at: Optional[datetime] = None,
    until_created_at: Optional[datetime] = None,
) -> int:
    """Get total count of finalized transactions to scan."""
    query = "SELECT COUNT(*) FROM transactions WHERE status = 'FINALIZED'"
    params = []

    if from_created_at:
        query += " AND created_at > %s"
        params.append(from_created_at)

    if until_created_at:
        query += " AND created_at < %s"
        params.append(until_created_at)

    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()[0]


def fetch_error_transactions(
    conn,
    from_created_at: Optional[datetime] = None,
    until_created_at: Optional[datetime] = None,
    batch_size: int = 100,
    total_to_scan: int = 0,
):
    """
    Fetch finalized transactions that have error results or undetermined consensus.

    Yields tuples of (batch, scanned_count) where batch is a list of transaction dicts.
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
            consensus_data,
            consensus_history
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
        cur.itersize = (
            batch_size * 10
        )  # Fetch more rows at a time from server for efficiency
        cur.execute(query, params)

        batch = []
        scanned_count = 0
        last_progress_report = 0

        for row in cur:
            scanned_count += 1
            tx = dict(row)

            # Report scanning progress every 1000 transactions
            if total_to_scan > 0 and scanned_count - last_progress_report >= 1000:
                progress = (scanned_count / total_to_scan) * 100
                log(
                    f"  Scanning progress: {scanned_count:,}/{total_to_scan:,} ({progress:.1f}%)"
                )
                last_progress_report = scanned_count

            # Filter for error/timeout/undetermined transactions (not success)
            result = extract_execution_result(tx)
            if result in ("error", "timeout", "undetermined"):
                batch.append(tx)
                if len(batch) >= batch_size:
                    yield batch, scanned_count
                    batch = []

        if batch:
            yield batch, scanned_count


def extract_execution_result(tx: dict) -> str:
    """
    Extract execution result from status, consensus_data, and consensus_history.

    Returns one of: "success", "error", "timeout", "undetermined"

    Priority:
    1. Original status timeout (LEADER_TIMEOUT, VALIDATORS_TIMEOUT) -> "timeout"
    2. Original status undetermined (UNDETERMINED) -> "undetermined"
    3. consensus_history.consensus_results[-1].consensus_round == "Undetermined" -> "undetermined"
    4. execution_result from consensus_data.leader_receipt[0] -> "success" or "error"
    5. Default -> "success"
    """
    consensus_data = tx.get("consensus_data")
    consensus_history = tx.get("consensus_history")

    # Check original status for timeout/undetermined
    original_status = tx.get("status")
    if original_status in ("LEADER_TIMEOUT", "VALIDATORS_TIMEOUT"):
        return "timeout"
    if original_status == "UNDETERMINED":
        return "undetermined"

    # Check if consensus was undetermined from consensus_history
    if (
        consensus_history is not None
        and "consensus_results" in consensus_history
        and len(consensus_history["consensus_results"]) > 0
    ):
        last_round = consensus_history["consensus_results"][-1]
        if last_round.get("consensus_round") == "Undetermined":
            return "undetermined"

    # Try to get execution_result from leader receipt
    if consensus_data is not None:
        leader_receipts = consensus_data.get("leader_receipt", [])
        if leader_receipts and len(leader_receipts) > 0:
            first_receipt = leader_receipts[0]
            if first_receipt is not None and isinstance(first_receipt, dict):
                execution_result = first_receipt.get("execution_result")
                if execution_result is not None:
                    # Convert to lowercase for consistency (stored as "SUCCESS" or "ERROR")
                    return str(execution_result).lower()

    # Default to success
    return "success"


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
    # Status is always "success" since we only process finalized transactions
    tx_status = "success"
    processing_time_ms = calculate_processing_time_ms(tx)
    llm_calls = extract_llm_calls(tx.get("consensus_data"))
    # Result can be: success, error, timeout, undetermined
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


def send_to_api(
    api_url: str, api_key: str, decisions: list, max_retries: int = 3
) -> tuple[bool, str]:
    """
    Send decisions batch to the API with retry logic.

    Returns tuple of (success, error_message).
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {"decisions": decisions}

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                f"{api_url}/api/ingest",
                json=payload,
                headers=headers,
                timeout=60,  # Increased timeout for large batches
            )

            if response.status_code == 200:
                return True, ""

            error_msg = (
                f"API returned status {response.status_code}: {response.text[:200]}"
            )
            if attempt < max_retries:
                log(f"  Retry {attempt}/{max_retries}: {error_msg}")
                time.sleep(2**attempt)  # Exponential backoff
            else:
                return False, error_msg

        except requests.Timeout:
            error_msg = "API request timed out"
            if attempt < max_retries:
                log(f"  Retry {attempt}/{max_retries}: {error_msg}")
                time.sleep(2**attempt)
            else:
                return False, error_msg

        except Exception as e:
            error_msg = f"Error: {e}"
            if attempt < max_retries:
                log(f"  Retry {attempt}/{max_retries}: {error_msg}")
                time.sleep(2**attempt)
            else:
                return False, error_msg

    return False, "Max retries exceeded"


def main():
    parser = argparse.ArgumentParser(
        description="Update error/timeout/undetermined transactions in usage metrics service"
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

    # Start timing
    start_time = time.time()

    # Connect to database
    log(f"Connecting to database at {args.db_host}:{args.db_port}...")
    conn = get_db_connection(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        database=args.db_name,
    )
    log("Database connection established")

    # Get from_created_at if hash provided
    from_created_at = None
    if args.from_hash:
        from_created_at = get_transaction_created_at(conn, args.from_hash)
        if from_created_at is None:
            log(f"ERROR: Transaction with hash {args.from_hash} not found")
            sys.exit(1)
        log(
            f"Processing transactions after {from_created_at} (excluding hash: {args.from_hash})"
        )

    # Get until_created_at if hash provided
    until_created_at = None
    if args.until_hash:
        until_created_at = get_transaction_created_at(conn, args.until_hash)
        if until_created_at is None:
            log(f"ERROR: Transaction with hash {args.until_hash} not found")
            sys.exit(1)
        log(
            f"Processing transactions before {until_created_at} (excluding hash: {args.until_hash})"
        )

    # Get total count for progress tracking
    log("Counting total transactions to scan...")
    total_to_scan = get_total_transaction_count(conn, from_created_at, until_created_at)
    log(f"Total finalized transactions to scan: {total_to_scan:,}")

    if total_to_scan == 0:
        log("No transactions to process")
        conn.close()
        return

    log(
        "Scanning for transactions with result='error', 'timeout', or 'undetermined'..."
    )
    if args.dry_run:
        log("MODE: DRY RUN (no API calls will be made)")

    # Process transactions in batches
    total_matched = 0
    total_sent = 0
    total_failed = 0
    total_scanned = 0
    batch_count = 0
    failed_hashes = []
    result_counts = {"error": 0, "timeout": 0, "undetermined": 0}

    for batch, scanned_count in fetch_error_transactions(
        conn,
        from_created_at,
        until_created_at,
        batch_size=args.batch_size,
        total_to_scan=total_to_scan,
    ):
        batch_count += 1
        total_scanned = scanned_count
        decisions = [build_decision_payload(tx) for tx in batch]
        total_matched += len(decisions)

        # Count results by type
        for tx in batch:
            result = extract_execution_result(tx)
            if result in result_counts:
                result_counts[result] += 1

        # Calculate progress
        elapsed = time.time() - start_time
        scan_progress = (
            (total_scanned / total_to_scan) * 100 if total_to_scan > 0 else 0
        )

        if args.dry_run:
            if args.verbose:
                for decision in decisions:
                    print(json.dumps(decision, indent=2, default=str), flush=True)
            log(
                f"[DRY RUN] Batch {batch_count}: Would send {len(decisions)} decisions | "
                f"Matched: {total_matched:,} | Scanned: {total_scanned:,}/{total_to_scan:,} ({scan_progress:.1f}%)"
            )
        else:
            success, error_msg = send_to_api(args.api_url, args.api_key, decisions)
            if success:
                total_sent += len(decisions)
                # Calculate ETA based on scan progress
                if scan_progress > 0:
                    eta_seconds = (elapsed / scan_progress) * (100 - scan_progress)
                    eta_str = format_duration(eta_seconds)
                else:
                    eta_str = "calculating..."

                log(
                    f"Batch {batch_count}: Sent {len(decisions)} decisions | "
                    f"Total sent: {total_sent:,} | Scanned: {total_scanned:,}/{total_to_scan:,} ({scan_progress:.1f}%) | "
                    f"Elapsed: {format_duration(elapsed)} | ETA: {eta_str}"
                )
            else:
                total_failed += len(decisions)
                # Track failed transaction hashes
                for tx in batch:
                    failed_hashes.append(tx["hash"])
                log(
                    f"Batch {batch_count}: FAILED to send {len(decisions)} decisions | "
                    f"Error: {error_msg} | Total failed: {total_failed:,}"
                )

    conn.close()

    # Final timing
    total_elapsed = time.time() - start_time

    # Summary
    print("\n" + "=" * 60, flush=True)
    log("SUMMARY")
    print("=" * 60, flush=True)
    print(f"  Total transactions scanned: {total_scanned:,}", flush=True)
    print(
        f"  Total matched (error/timeout/undetermined): {total_matched:,}", flush=True
    )
    print(f"    - Errors: {result_counts['error']:,}", flush=True)
    print(f"    - Timeouts: {result_counts['timeout']:,}", flush=True)
    print(f"    - Undetermined: {result_counts['undetermined']:,}", flush=True)
    print(f"  Total batches processed: {batch_count}", flush=True)
    print(f"  Total time: {format_duration(total_elapsed)}", flush=True)

    if args.dry_run:
        print("  Mode: DRY RUN (no API calls made)", flush=True)
    else:
        print(f"  Successfully sent: {total_sent:,}", flush=True)
        print(f"  Failed: {total_failed:,}", flush=True)
        if total_matched > 0:
            success_rate = (total_sent / total_matched) * 100
            print(f"  Success rate: {success_rate:.1f}%", flush=True)

        if failed_hashes:
            print(f"\n  Failed transaction hashes (first 10):", flush=True)
            for h in failed_hashes[:10]:
                print(f"    - {h}", flush=True)
            if len(failed_hashes) > 10:
                print(f"    ... and {len(failed_hashes) - 10} more", flush=True)

    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
