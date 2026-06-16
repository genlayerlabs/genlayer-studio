# Terminal Contract Snapshot Pruning

Studio can archive terminal transaction `contract_snapshot` payloads to S3 before
pruning them from the hot `transactions` table. This is intended for deployments
where terminal historical rollback snapshots are not needed in normal API reads,
but a cheap recovery copy is still useful.

The feature is disabled by default.

## Runtime Auto-Pruner

Enable on the RPC service:

```bash
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_BUCKET=<bucket>
STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_PREFIX=studio/terminal-contract-snapshots
STUDIO_CONTRACT_SNAPSHOT_PRUNER_RETENTION_HOURS=24
STUDIO_CONTRACT_SNAPSHOT_PRUNER_BATCH_SIZE=5
STUDIO_CONTRACT_SNAPSHOT_PRUNER_INTERVAL_SECONDS=300
```

Each batch:

1. Locks a small set of `FINALIZED` or `CANCELED` rows with
   `contract_snapshot IS NOT NULL`.
2. Writes each snapshot to S3 as deterministic gzip JSON:
   `<prefix>/v1/<hash-shard>/<tx-hash>.contract_snapshot.json.gz`.
3. Includes S3 checksum and metadata with transaction hash, status, and payload
   hashes.
4. Sets `transactions.contract_snapshot = NULL` only after the S3 write succeeds.

If the S3 write fails, the row is not pruned.

## One-Time Historical Drain

Use the same implementation for one-time cleanup:

```bash
python -m backend.database_handler.prune_terminal_snapshots \
  --batch-size 5 \
  --retention-hours 24
```

Dry-run without S3 credentials:

```bash
STUDIO_CONTRACT_SNAPSHOT_PRUNER_DRY_RUN=true \
python -m backend.database_handler.prune_terminal_snapshots --dry-run --max-batches 10
```

Set `--max-batches` for controlled partial runs. Omit it or pass `0` to continue
until no eligible rows remain.

## Reclaiming Disk

Pruning removes logical JSONB payloads, but Postgres/RDS may not immediately
return physical storage. Plan a separate compaction/rebuild step if the goal is
to lower allocated database storage.
