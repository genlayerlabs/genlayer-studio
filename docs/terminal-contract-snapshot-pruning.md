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
The fastest production drain should run as a three-phase pipeline:

1. Archive snapshots and write archive index rows.
2. Verify archived objects by reading them back and setting `verified_at`.
3. Prune only hot snapshots whose archive row has been verified.

The legacy/default `full` CLI phase still performs archive, read-back
verification, and pruning in one pass for small/manual runs.

## Runtime Auto-Pruner

GCS example for current GCP production:

```bash
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED=true
STUDIO_CONTRACT_SNAPSHOT_PRUNER_VERIFY_ARCHIVE=true
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
STUDIO_CONTRACT_SNAPSHOT_PRUNER_VERIFY_ARCHIVE=true
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
STUDIO_CONTRACT_SNAPSHOT_PRUNER_VERIFY_ARCHIVE=true
STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED=true
```

The default `full` phase performs all work in one batch:

1. Locks a small set of `FINALIZED` or `CANCELED` rows with
   `contract_snapshot IS NOT NULL`.
2. Writes each snapshot to the configured backend as deterministic gzip JSON.
3. Reads the archived object back and verifies the compressed checksum, gzip
   payload, and raw JSON checksum.
4. Stores object location, checksums, and `verified_at` in
   `transaction_snapshot_archives`.
5. Sets `transactions.contract_snapshot = NULL` only after the archive write,
   read-back verification, and index row succeed.

If the archive write or read-back verification fails, the row is not pruned.

`STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED=false` is treated as a lossy
mode and is rejected unless
`STUDIO_CONTRACT_SNAPSHOT_PRUNER_ALLOW_LOSSY_PRUNE=true` is also set.

## One-Time Historical Drain

For large one-time cleanup, prefer the split pipeline. Keep the background
pruner disabled while these commands run.

Archive phase, no deletion:

```bash
python -m backend.database_handler.prune_terminal_snapshots \
  --phase archive \
  --batch-size 25 \
  --retention-hours 24 \
  --workers 4 \
  --max-batches 100 \
  --sleep-seconds 0
```

Verify phase, no deletion:

```bash
python -m backend.database_handler.prune_terminal_snapshots \
  --phase verify \
  --batch-size 25 \
  --workers 4 \
  --max-batches 100 \
  --sleep-seconds 0
```

Prune phase, deletes only verified archive rows:

```bash
python -m backend.database_handler.prune_terminal_snapshots \
  --phase prune \
  --batch-size 100 \
  --retention-hours 24 \
  --workers 4 \
  --max-batches 100 \
  --sleep-seconds 0
```

Use the legacy all-in-one phase for tiny/manual checks:

```bash
python -m backend.database_handler.prune_terminal_snapshots \
  --phase full \
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

Use `--workers` for bounded parallel one-time drains:

```bash
python -m backend.database_handler.prune_terminal_snapshots \
  --phase archive \
  --batch-size 5 \
  --retention-hours 24 \
  --workers 4 \
  --max-batches 100 \
  --sleep-seconds 0
```

Each worker uses its own database session and the shared queue queries use
`FOR UPDATE SKIP LOCKED`, so workers claim different rows/archive rows. The CLI
caps its database connection pool to the worker count.

## Read-Through Retrieval

Set `STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED=true` on RPC services to
hydrate archived snapshots on direct transaction reads. The read path verifies
the compressed object checksum, decompresses the payload, verifies the raw JSON
checksum, and returns the snapshot as if it had been read from Postgres.

This read-through is intentionally limited to direct transaction reads. Broad
transaction listings do not retrieve archived snapshots.

## Stable 121.x RPC State Scope

The stable `v0.121` line should keep the transaction response shape stable while
removing accidental large state payloads from ETH-compatible reads.

Current stable scope:

- `gen_call` accepts an additive `status` parameter with public values
  `decided` and `finalized`.
- `status=decided` maps to Studio's current internal accepted/decided state
  bucket. `status=finalized` maps to finalized state.
- The legacy Studio `transaction_hash_variant` selector remains accepted for
  compatibility. `latest-final` maps to finalized state; omitted or other
  values keep the previous decided-state behavior.
- ETH-compatible responses do not include Studio `contract_snapshot` payloads:
  `eth_getTransactionByHash`, `eth_getTransactionReceipt`,
  `eth_getBlockByNumber`, and `eth_getBlockByHash`.
- Explicit Studio/debug direct reads can still request and hydrate archived
  `contract_snapshot` data where that API is intended to expose full state.

Do not add broad historical state semantics to `v0.121` as a compatibility
patch. A stable patch can remove accidental state payloads and add the correct
new selector, but it should not change execution semantics or require client
library shape changes.

## Next-Version Historical State Scope

The next version should make historical state behavior explicit instead of
relying on the current mutable `current_state` lookup.

Target behavior:

- A transaction records the activation block/state point used for execution.
- Cross-contract reads during execution resolve against that locked activation
  block so validators read the same historical view.
- `gen_call` supports calling at a past block/state point, with `status`
  constrained to `decided` or `finalized`.
- Node, Studio, CLI, and client libraries should converge on `decided` and
  `finalized`; node's older `accepted` selector should be migrated in the next
  release.
- The historical resolver should work against hot state first and archived
  state second, with read-through hidden behind the storage abstraction.

## Throughput Sizing

The current pruner is correctness-first. Each batch locks candidate rows and
then processes each row sequentially:

1. Read `contract_snapshot::text` and `pg_column_size(contract_snapshot)` from
   Postgres.
2. Serialize and gzip the snapshot.
3. Write the gzip object to the archive backend.
4. Read the object back for verification.
5. Insert or update the archive index row.
6. Set `transactions.contract_snapshot = NULL`.

That means a large one-time drain is bounded by Postgres read throughput,
compression throughput, S3 PUT latency, S3 GET verification latency, and the
final Postgres update volume. The verify step intentionally doubles object-store
read/write traffic for compressed bytes, but it does not double the Postgres
read volume.

Rough 2 TB logical hot-state drain estimates:

```text
Sustained logical throughput   Approximate wall time
10 MB/s                        56 hours
25 MB/s                        22 hours
50 MB/s                        11 hours
100 MB/s                       5.6 hours
```

Object count can dominate if snapshots are small. At 100 ms of sequential
archive/verify overhead per object, one million snapshots adds about 28 hours
before accounting for bytes. For Rally-scale drains, measure row count and size
distribution before deciding whether the one-worker implementation is enough or
whether to add bounded parallel archive workers.

The one-time CLI logs per-batch and total elapsed time plus logical and
compressed throughput. For production-size drains, use `--sleep-seconds 0` only
inside an approved maintenance/controlled run; the default sleep is intentionally
gentle and can add meaningful wall time across many batches.

Recommended Rally measurement before a production drain:

```sql
SELECT
  count(*) AS eligible_rows,
  pg_size_pretty(sum(pg_column_size(contract_snapshot))) AS logical_size,
  percentile_disc(0.50) WITHIN GROUP (ORDER BY pg_column_size(contract_snapshot)) AS p50_bytes,
  percentile_disc(0.90) WITHIN GROUP (ORDER BY pg_column_size(contract_snapshot)) AS p90_bytes,
  percentile_disc(0.99) WITHIN GROUP (ORDER BY pg_column_size(contract_snapshot)) AS p99_bytes,
  max(pg_column_size(contract_snapshot)) AS max_bytes
FROM transactions
WHERE contract_snapshot IS NOT NULL
  AND status IN ('FINALIZED', 'CANCELED');
```

Also sample real compression ratio on production-like rows before estimating S3
bytes and cost. The safe default is still `VERIFY_ARCHIVE=true`; if the drain is
too slow, optimize with measured parallelism rather than removing verification
as the first lever.

### Rally Production Measurement 2026-06-19

Read-only measurements against Rally production on 2026-06-19:

- Cloud SQL instance: PostgreSQL 17, regional, PD_SSD, 3850 GB allocated.
- `transactions` table total size: about 3.80 TB.
- `transactions` TOAST size: about 3.80 TB.
- Live rows: about 227k.
- Eligible terminal rows with snapshots: 227,684.
- Eligible `pg_column_size(contract_snapshot)` total: 2,429,083,876,044 bytes
  (about 2.21 TiB).
- Snapshot size distribution by `pg_column_size`: p50 5.3 MB, p90 27.4 MB,
  p99 71.2 MB, max 87.3 MB.
- Sampled gzip archive ratio: about 0.61 of `pg_column_size`, implying roughly
  1.35 TiB of compressed archive objects before verification reads.
- Single 4-CPU JSON-RPC pod sample: DB fetch plus gzip/checksum was about
  10.8 MiB/s against `pg_column_size`; gzip/checksum alone was about
  14.7 MiB/s.
- Read-only parallel fetch plus gzip/checksum probe against 48 sampled snapshots:
  1 worker 11.2 MiB/s, 2 workers 20.6 MiB/s, 4 workers 37.5 MiB/s, 8 workers
  34.8 MiB/s. The 8-worker run showed higher summed fetch time, so 4 workers
  looked like the local knee for this pod/sample.
- A second 4-worker probe biased to snapshots above 5 MB measured 37.1 MiB/s.

Implications:

- One-object-per-snapshot is not obviously wasteful for Rally because compressed
  objects average several MB, well above small-object minimum billing thresholds.
- A single sequential worker is likely a multi-day drain. The read-only probe
  implies about 58 hours at 1 worker and about 17 hours at 4 workers before
  object-store write/read-back overhead. Use controlled parallel workers/jobs
  after the candidate index is deployed and remeasure actual archive/prune
  throughput before going wider than 4 workers.
- Use the one-time CLI with `--workers`, `--max-batches`, and `--sleep-seconds 0`
  for the benchmark ladder. Keep each first run small enough that rollback is
  operationally boring, then scale only while DB CPU, DB IO, object-store errors,
  and RPC latency remain healthy.
- The current production database will not immediately return allocated disk
  after pruning. The AWS migration/fresh restore is the right time to materialize
  the smaller database size.

### Studio Dev Full Drain Validation 2026-06-19

`studio-dev` was validated with a full one-time archive/verify/prune drain on
2026-06-19. Background pruning remained disabled.

Runtime configuration:

- Backend: `s3`
- Bucket: `devexp-dev-studio-snapshot-archives`
- Prefix: `studio-dev/terminal-contract-snapshots`
- Storage class: `STANDARD_IA`
- Archive verification: enabled
- Retrieval/read-through: enabled

Command:

```bash
python3 -m backend.database_handler.prune_terminal_snapshots \
  --batch-size 5 \
  --retention-hours 0 \
  --sleep-seconds 0
```

Result:

- Before run: 17 eligible terminal snapshots, 2,720 logical bytes.
- Run completed in 4 batches: 17 candidates, 17 archived, 17 pruned.
- Written compressed bytes: 2,159.
- After run: 0 remaining eligible terminal snapshots.
- All 17 hot transaction rows had `contract_snapshot IS NULL`.
- All 17 archive rows had `archive_status='pruned'` and `backend='s3'`.
- All 17 S3 objects were fetched, gzip-decoded, and verified against archive
  row checksums.
- `eth_getTransactionByHash` for a pruned transaction hydrated the archived
  snapshot through the read path.
- `/health` was healthy and `eth_chainId` returned `0xf22d` after the run.

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

## Production Rollout Checklist

Use this checklist for each Studio namespace. Keep the background pruner disabled
until the one-time drain has been measured and the steady-state settings are
chosen.

### 1. Preflight

- Confirm the deployed image contains this pruning code and migrations.
- Confirm `transaction_snapshot_archives` exists.
- Confirm `idx_transactions_terminal_snapshot_archive_candidates` exists and is
  valid.
- Confirm object-storage credentials from the JSON-RPC pod or one-time job:
  write, read, and, if applicable, KMS encrypt/decrypt.
- Confirm RPC services have
  `STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED=true`.
- Confirm background pruning is off:
  `STUDIO_CONTRACT_SNAPSHOT_PRUNER_ENABLED=false`.
- Confirm object lifecycle/retention policy is intentional for the archive
  bucket or prefix.
- Record the candidate count and byte distribution with the Rally measurement
  query above.

Index check:

```sql
SELECT
  indexrelid::regclass AS index_name,
  indisvalid,
  indisready
FROM pg_index
WHERE indexrelid::regclass::text =
  'idx_transactions_terminal_snapshot_archive_candidates';
```

Archive table check:

```sql
SELECT to_regclass('public.transaction_snapshot_archives') AS archive_table;
```

### 2. Rollout Ladder

Start with lossless archive verification enabled. Do not set
`STUDIO_CONTRACT_SNAPSHOT_PRUNER_ALLOW_LOSSY_PRUNE=true` for production drains.

1. Deploy code and migrations with pruning off.
2. Enable archive retrieval on RPC services.
3. Run a dry-run:

   ```bash
   python -m backend.database_handler.prune_terminal_snapshots \
     --phase archive \
     --dry-run \
     --batch-size 5 \
     --retention-hours 24 \
     --max-batches 10 \
     --workers 1 \
     --sleep-seconds 0
   ```

4. Run one real batch:

   ```bash
   python -m backend.database_handler.prune_terminal_snapshots \
     --phase full \
     --batch-size 1 \
     --retention-hours 24 \
     --max-batches 1 \
     --workers 1 \
     --sleep-seconds 0
   ```

5. Verify the pruned transaction end to end:
   archive row, object metadata, checksum, hot row null, and direct read
   hydration.
6. For the real historical drain, switch to the split pipeline:

   ```bash
   python -m backend.database_handler.prune_terminal_snapshots \
     --phase archive \
     --batch-size 25 \
     --retention-hours 24 \
     --max-batches 100 \
     --workers 4 \
     --sleep-seconds 0

   python -m backend.database_handler.prune_terminal_snapshots \
     --phase verify \
     --batch-size 25 \
     --max-batches 100 \
     --workers 4 \
     --sleep-seconds 0

   python -m backend.database_handler.prune_terminal_snapshots \
     --phase prune \
     --batch-size 100 \
     --retention-hours 24 \
     --max-batches 100 \
     --workers 4 \
     --sleep-seconds 0
   ```

7. Run a small measured batch ladder while watching DB CPU/IO, object-store
   errors, RPC latency, and application logs:
   `workers=1`, then `workers=2`, then `workers=4`. Use dedicated one-off
   Kubernetes Jobs for large drains instead of execing inside serving RPC pods.
8. Continue the one-time drain only at the highest worker/job count that remains
   healthy. Keep verification and pruning behind the archive phase; do not prune
   rows whose archive row lacks `verified_at`.
9. After the historical drain, enable the background pruner only if steady-state
   pruning is desired. Use a conservative retention window and batch size first,
   for example `retention_hours=24`, `batch_size=5`, `interval_seconds=300`.

### 3. Per-Batch Validation

Use these checks after a tiny real batch and periodically during larger drains.

Archive row:

```sql
SELECT
  tx_hash,
  archive_status,
  backend,
  bucket,
  object_key,
  snapshot_sha256,
  compressed_sha256,
  snapshot_bytes,
  compressed_bytes,
  archived_at,
  pruned_at
FROM transaction_snapshot_archives
WHERE tx_hash = '<tx-hash>';
```

Hot row:

```sql
SELECT
  hash,
  status,
  contract_snapshot IS NULL AS snapshot_pruned
FROM transactions
WHERE hash = '<tx-hash>';
```

Progress:

```sql
SELECT
  count(*) AS remaining_rows,
  pg_size_pretty(sum(pg_column_size(contract_snapshot))) AS remaining_logical_size
FROM transactions
WHERE contract_snapshot IS NOT NULL
  AND status IN ('FINALIZED', 'CANCELED');
```

Archive totals:

```sql
SELECT
  archive_status,
  backend,
  count(*) AS rows,
  pg_size_pretty(sum(snapshot_bytes)) AS logical_size,
  pg_size_pretty(sum(compressed_bytes)) AS compressed_size
FROM transaction_snapshot_archives
GROUP BY archive_status, backend
ORDER BY archive_status, backend;
```

### 4. Rollback And Stop Conditions

Immediate stop switches:

- Stop the one-time job.
- Keep or set `STUDIO_CONTRACT_SNAPSHOT_PRUNER_ENABLED=false`.
- If archive reads are causing user-visible RPC issues, set
  `STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED=false`. This disables
  hydration but does not restore hot snapshots.

Stop the drain and investigate if any of these happen:

- Archive write or read-back verification errors.
- Checksum mismatch.
- Sustained object-store throttling or 5xx errors.
- DB CPU/IO saturation or material RPC latency regression.
- Pruned transaction cannot hydrate through the direct read path.
- Archive rows are missing for pruned hot rows.

Rollback from a successful prune is restore-oriented: the lossless copy is the
archive object plus `transaction_snapshot_archives` metadata. If hot-state
restoration is required, write a targeted restore job that loads verified
archive objects and updates `transactions.contract_snapshot` for selected hashes.
Do not delete archive objects during or immediately after rollout.

## Reclaiming Disk

Pruning removes logical JSONB payloads and reduces future database growth, but
Postgres/Cloud SQL/RDS may not immediately return physical storage to the
provider. Plan a separate compaction/rebuild step if the goal is to reduce
allocated database storage on an existing instance. A database migration to a
fresh AWS instance is a natural opportunity to materialize the smaller size.
