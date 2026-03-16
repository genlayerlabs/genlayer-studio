# Consensus-Worker Benchmarks

This folder contains local synthetic benchmarks for consensus-worker storage access paths.

## Scripts

- `snapshot_bench.py`: single-execution storage read scenarios (local-heavy and mixed cross-contract reads).
- `validator_batch_bench.py`: leader + validator batch simulation to quantify cache-sharing effects.

## Usage

From repo root:

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmarks/snapshot_bench.py
```

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmarks/validator_batch_bench.py --factory-delay-ms 1.0
```

Notes:

- These scripts are not wired into CI.
- They are intended for side-by-side local comparisons between commits/branches.
