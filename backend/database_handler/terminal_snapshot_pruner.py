from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)


TERMINAL_STATUSES = ("FINALIZED", "CANCELED")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning(
            "%s=%s below minimum %s; using %s", name, value, minimum, default
        )
        return default
    return value


@dataclass(frozen=True)
class TerminalSnapshotPrunerConfig:
    enabled: bool = False
    archive_enabled: bool = True
    dry_run: bool = False
    batch_size: int = 5
    retention_hours: int = 24
    interval_seconds: int = 300
    s3_bucket: str | None = None
    s3_prefix: str = "studio/terminal-contract-snapshots"
    s3_region: str | None = None
    s3_storage_class: str | None = None
    s3_sse: str | None = None
    s3_kms_key_id: str | None = None

    @classmethod
    def from_environment(cls) -> "TerminalSnapshotPrunerConfig":
        return cls(
            enabled=_env_bool("STUDIO_CONTRACT_SNAPSHOT_PRUNER_ENABLED"),
            archive_enabled=_env_bool(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED", default=True
            ),
            dry_run=_env_bool("STUDIO_CONTRACT_SNAPSHOT_PRUNER_DRY_RUN"),
            batch_size=_env_int("STUDIO_CONTRACT_SNAPSHOT_PRUNER_BATCH_SIZE", 5, 1),
            retention_hours=_env_int(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_RETENTION_HOURS", 24, 0
            ),
            interval_seconds=_env_int(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_INTERVAL_SECONDS", 300, 1
            ),
            s3_bucket=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_BUCKET") or None,
            s3_prefix=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_PREFIX")
            or "studio/terminal-contract-snapshots",
            s3_region=os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_REGION")
            or None,
            s3_storage_class=os.getenv(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_STORAGE_CLASS"
            )
            or None,
            s3_sse=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_SSE") or None,
            s3_kms_key_id=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_KMS_KEY_ID")
            or None,
        )

    def validate_for_run(self) -> None:
        if self.archive_enabled and not self.dry_run and not self.s3_bucket:
            raise RuntimeError(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_BUCKET is required when "
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED=true"
            )


@dataclass(frozen=True)
class SnapshotCandidate:
    tx_hash: str
    status: str
    created_at: datetime | None
    snapshot_bytes: int
    snapshot_json: str


@dataclass(frozen=True)
class ArchiveResult:
    bucket: str
    key: str
    uncompressed_bytes: int
    compressed_bytes: int
    uncompressed_sha256: str
    compressed_sha256: str


@dataclass(frozen=True)
class PruneBatchResult:
    candidates: int = 0
    archived: int = 0
    pruned: int = 0
    logical_bytes: int = 0
    compressed_bytes: int = 0
    dry_run: bool = False


class SnapshotArchiveWriter(Protocol):
    def archive(self, candidate: SnapshotCandidate) -> ArchiveResult: ...


class S3SnapshotArchiveWriter:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        region: str | None = None,
        storage_class: str | None = None,
        sse: str | None = None,
        kms_key_id: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region
        self.storage_class = storage_class
        self.sse = sse
        self.kms_key_id = kms_key_id
        self._client = client

    @classmethod
    def from_config(cls, config: TerminalSnapshotPrunerConfig):
        if not config.s3_bucket:
            raise RuntimeError("S3 bucket is required for snapshot archiving")
        return cls(
            bucket=config.s3_bucket,
            prefix=config.s3_prefix,
            region=config.s3_region,
            storage_class=config.s3_storage_class,
            sse=config.s3_sse,
            kms_key_id=config.s3_kms_key_id,
        )

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - depends on image deps
                raise RuntimeError(
                    "boto3 is required for S3 contract snapshot archiving"
                ) from exc
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def key_for_hash(self, tx_hash: str) -> str:
        normalized = tx_hash.lower().removeprefix("0x")
        shard = normalized[:2] if len(normalized) >= 2 else "unknown"
        filename = f"{tx_hash}.contract_snapshot.json.gz"
        if not self.prefix:
            return f"v1/{shard}/{filename}"
        return f"{self.prefix}/v1/{shard}/{filename}"

    def archive(self, candidate: SnapshotCandidate) -> ArchiveResult:
        raw = candidate.snapshot_json.encode("utf-8")
        body = gzip.compress(raw, compresslevel=6, mtime=0)
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        compressed_digest = hashlib.sha256(body).digest()
        compressed_sha256 = compressed_digest.hex()
        key = self.key_for_hash(candidate.tx_hash)

        put_kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentEncoding": "gzip",
            "ContentType": "application/json",
            "ChecksumSHA256": base64.b64encode(compressed_digest).decode("ascii"),
            "Metadata": {
                "schema-version": "1",
                "tx-hash": candidate.tx_hash,
                "tx-status": candidate.status,
                "snapshot-sha256": raw_sha256,
                "compressed-sha256": compressed_sha256,
                "snapshot-bytes": str(candidate.snapshot_bytes),
            },
        }
        if self.storage_class:
            put_kwargs["StorageClass"] = self.storage_class
        if self.sse:
            put_kwargs["ServerSideEncryption"] = self.sse
        if self.kms_key_id:
            put_kwargs["SSEKMSKeyId"] = self.kms_key_id

        self.client.put_object(**put_kwargs)

        return ArchiveResult(
            bucket=self.bucket,
            key=key,
            uncompressed_bytes=len(raw),
            compressed_bytes=len(body),
            uncompressed_sha256=raw_sha256,
            compressed_sha256=compressed_sha256,
        )


class TerminalSnapshotPruner:
    def __init__(
        self,
        get_session: Callable[[], Session],
        config: TerminalSnapshotPrunerConfig,
        archive_writer: SnapshotArchiveWriter | None = None,
    ) -> None:
        self.get_session = get_session
        self.config = config
        self.archive_writer = archive_writer

    def _archive_writer(self) -> SnapshotArchiveWriter:
        if self.archive_writer is None:
            self.archive_writer = S3SnapshotArchiveWriter.from_config(self.config)
        return self.archive_writer

    def _cutoff(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(hours=self.config.retention_hours)

    def _fetch_candidates(self, session: Session) -> list[SnapshotCandidate]:
        rows = (
            session.execute(
                text(
                    """
                    SELECT
                        hash,
                        status::text AS status,
                        created_at,
                        pg_column_size(contract_snapshot) AS snapshot_bytes,
                        contract_snapshot::text AS snapshot_json
                    FROM transactions
                    WHERE contract_snapshot IS NOT NULL
                      AND status IN ('FINALIZED', 'CANCELED')
                      AND created_at < :cutoff
                    ORDER BY created_at ASC, hash ASC
                    LIMIT :batch_size
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {
                    "cutoff": self._cutoff(),
                    "batch_size": self.config.batch_size,
                },
            )
            .mappings()
            .all()
        )

        return [
            SnapshotCandidate(
                tx_hash=row["hash"],
                status=row["status"],
                created_at=row["created_at"],
                snapshot_bytes=int(row["snapshot_bytes"] or 0),
                snapshot_json=row["snapshot_json"],
            )
            for row in rows
        ]

    def prune_once(self) -> PruneBatchResult:
        self.config.validate_for_run()
        session = self.get_session()
        archived = 0
        pruned = 0
        logical_bytes = 0
        compressed_bytes = 0
        try:
            candidates = self._fetch_candidates(session)
            if not candidates:
                session.rollback()
                return PruneBatchResult(dry_run=self.config.dry_run)

            if self.config.dry_run:
                logical_bytes = sum(
                    candidate.snapshot_bytes for candidate in candidates
                )
                session.rollback()
                return PruneBatchResult(
                    candidates=len(candidates),
                    logical_bytes=logical_bytes,
                    dry_run=True,
                )

            writer = self._archive_writer() if self.config.archive_enabled else None
            for candidate in candidates:
                logical_bytes += candidate.snapshot_bytes
                if writer is not None:
                    archive_result = writer.archive(candidate)
                    archived += 1
                    compressed_bytes += archive_result.compressed_bytes

                result = session.execute(
                    text(
                        """
                        UPDATE transactions
                        SET contract_snapshot = NULL
                        WHERE hash = :hash
                          AND contract_snapshot IS NOT NULL
                        """
                    ),
                    {"hash": candidate.tx_hash},
                )
                pruned += result.rowcount or 0

            session.commit()
            return PruneBatchResult(
                candidates=len(candidates),
                archived=archived,
                pruned=pruned,
                logical_bytes=logical_bytes,
                compressed_bytes=compressed_bytes,
                dry_run=False,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


async def run_terminal_snapshot_pruner_loop(
    get_session: Callable[[], Session],
    config: TerminalSnapshotPrunerConfig,
    archive_writer: SnapshotArchiveWriter | None = None,
) -> None:
    if not config.enabled:
        return

    config.validate_for_run()
    pruner = TerminalSnapshotPruner(get_session, config, archive_writer)
    logger.info(
        "Terminal contract snapshot pruner started "
        "(batch_size=%s retention_hours=%s archive_enabled=%s dry_run=%s)",
        config.batch_size,
        config.retention_hours,
        config.archive_enabled,
        config.dry_run,
    )

    while True:
        try:
            start = time.monotonic()
            result = await asyncio.to_thread(pruner.prune_once)
            elapsed = time.monotonic() - start
            if result.candidates:
                logger.info(
                    "Terminal contract snapshot pruning batch complete: "
                    "candidates=%s archived=%s pruned=%s logical_bytes=%s "
                    "compressed_bytes=%s dry_run=%s elapsed=%.2fs",
                    result.candidates,
                    result.archived,
                    result.pruned,
                    result.logical_bytes,
                    result.compressed_bytes,
                    result.dry_run,
                    elapsed,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Terminal contract snapshot pruning batch failed")

        await asyncio.sleep(config.interval_seconds)
