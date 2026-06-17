# Terminal Contract Snapshot Archiving

Studio can archive terminal transaction `contract_snapshot` payloads to cheap
object storage before pruning them from the hot `transactions` table. Direct
transaction reads can hydrate archived snapshots back from the archive, while
list/history endpoints avoid object-storage fanout.

The feature is disabled by default.

## Backends

Supported archive backends:

- `file`: local filesystem backend for development and tests.
- `gcs`: Google Cloud Storage, intended while production Studio is still on GCP.
- `s3`: Amazon S3, intended after the AWS migration.

All backends use the same deterministic gzip JSON object format:

```text
<prefix>/v1/<hash-shard>/<tx-hash>.contract_snapshot.json.gz
```

An archive index row is written to `transaction_snapshot_archives` with the
backend, object URI, uncompressed/compressed byte counts, and SHA-256 checksums.
The pruner only sets `transactions.contract_snapshot = NULL` after the archive
write and index row succeed.

## Runtime Auto-Pruner

GCS example for current GCP production:

```bash
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND=gcs
STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_BUCKET=<bucket>
STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_PREFIX=studio/terminal-contract-snapshots
STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_STORAGE_CLASS=NEARLINE
STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_RETENTION_HOURS=24
STUDIO_CONTRACT_SNAPSHOT_PRUNER_BATCH_SIZE=5
STUDIO_CONTRACT_SNAPSHOT_PRUNER_INTERVAL_SECONDS=300
```

S3 example for AWS:

```bash
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND=s3
STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_BUCKET=<bucket>
STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_PREFIX=studio/terminal-contract-snapshots
STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_STORAGE_CLASS=STANDARD_IA
STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_SSE=aws:kms
STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_KMS_KEY_ID=<kms-key-id>
STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED=true
```

Local development example:

```bash
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND=file
STUDIO_CONTRACT_SNAPSHOT_PRUNER_FILE_DIR=data/terminal-contract-snapshot-archive
STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED=true
```

Each batch:

1. Locks a small set of `FINALIZED` or `CANCELED` rows with
   `contract_snapshot IS NOT NULL`.
2. Writes each snapshot to the configured backend as deterministic gzip JSON.
3. Stores object location and checksums in `transaction_snapshot_archives`.
4. Sets `transactions.contract_snapshot = NULL` only after the archive write and
   index row succeed.

If the archive write fails, the row is not pruned.

## One-Time Historical Drain

Use the same implementation for one-time cleanup:

```bash
python -m backend.database_handler.prune_terminal_snapshots \
  --batch-size 5 \
  --retention-hours 24
```

Dry-run without object-storage credentials:

```bash
STUDIO_CONTRACT_SNAPSHOT_PRUNER_DRY_RUN=true \
python -m backend.database_handler.prune_terminal_snapshots --dry-run --max-batches 10
```

Set `--max-batches` for controlled partial runs. Omit it or pass `0` to continue
until no eligible rows remain.

## Read-Through Retrieval

Set `STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED=true` on RPC services to
hydrate archived snapshots on direct transaction reads. The read path verifies
the compressed object checksum, decompresses the payload, verifies the raw JSON
checksum, and returns the snapshot as if it had been read from Postgres.

This read-through is intentionally limited to direct transaction reads. Broad
transaction listings do not retrieve archived snapshots.

## GCP to AWS Migration

For the current migration plan, prefer:

1. Archive and prune in GCP using `gcs`.
2. Migrate the smaller Postgres database to AWS.
3. Keep AWS Studio reading the GCS archive temporarily.
4. Copy the GCS archive prefix to S3 in the background.
5. Update the archive index rows to point at the copied S3 objects.
6. Flip new archive writes to `s3` after the copy is verified.

Because the object key format is stable across backends, the metadata backfill
can be a bounded SQL update after the object copy is verified:

```sql
UPDATE transaction_snapshot_archives
SET backend = 's3',
    bucket = '<s3-bucket>',
    uri = 's3://<s3-bucket>/' || object_key
WHERE backend = 'gcs'
  AND bucket = '<gcs-bucket>'
  AND object_key LIKE 'studio/terminal-contract-snapshots/%';
```

This keeps the database migration smaller without coupling every GCP pruning
batch to cross-cloud object writes.

## Reclaiming Disk

Pruning removes logical JSONB payloads and reduces future database growth, but
Postgres/Cloud SQL/RDS may not immediately return physical storage to the
provider. Plan a separate compaction/rebuild step if the goal is to reduce
allocated database storage on an existing instance. A database migration to a
fresh AWS instance is a natural opportunity to materialize the smaller size.
