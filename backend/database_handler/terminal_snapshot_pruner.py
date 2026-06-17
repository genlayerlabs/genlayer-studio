from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)


TERMINAL_STATUSES = ("FINALIZED", "CANCELED")
ARCHIVE_FORMAT = "full-json-gzip-v1"
DEFAULT_ARCHIVE_PREFIX = "studio/terminal-contract-snapshots"
DEFAULT_FILE_ARCHIVE_DIR = "data/terminal-contract-snapshot-archive"


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


def snapshot_archive_read_through_enabled() -> bool:
    return _env_bool("STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED")


def _configured_archive_backend() -> str:
    backend = os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND")
    if backend:
        return backend.strip().lower()
    if os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_BUCKET"):
        return "gcs"
    return "s3"


@dataclass(frozen=True)
class TerminalSnapshotPrunerConfig:
    enabled: bool = False
    archive_enabled: bool = True
    dry_run: bool = False
    batch_size: int = 5
    retention_hours: int = 24
    interval_seconds: int = 300
    archive_backend: str = "s3"
    file_dir: str = DEFAULT_FILE_ARCHIVE_DIR
    gcs_bucket: str | None = None
    gcs_prefix: str = DEFAULT_ARCHIVE_PREFIX
    gcs_storage_class: str | None = None
    s3_bucket: str | None = None
    s3_prefix: str = DEFAULT_ARCHIVE_PREFIX
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
            archive_backend=_configured_archive_backend(),
            file_dir=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_FILE_DIR")
            or DEFAULT_FILE_ARCHIVE_DIR,
            gcs_bucket=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_BUCKET") or None,
            gcs_prefix=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_PREFIX")
            or DEFAULT_ARCHIVE_PREFIX,
            gcs_storage_class=os.getenv(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_STORAGE_CLASS"
            )
            or None,
            s3_bucket=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_BUCKET") or None,
            s3_prefix=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_PREFIX")
            or DEFAULT_ARCHIVE_PREFIX,
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
        if not self.archive_enabled or self.dry_run:
            return

        backend = self.archive_backend.lower()
        if backend not in {"file", "gcs", "s3"}:
            raise RuntimeError(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND must be one of "
                "file, gcs, or s3"
            )
        if backend == "file" and not self.file_dir:
            raise RuntimeError(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_FILE_DIR is required when "
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND=file"
            )
        if backend == "gcs" and not self.gcs_bucket:
            raise RuntimeError(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_BUCKET is required when "
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND=gcs"
            )
        if backend == "s3" and not self.s3_bucket:
            raise RuntimeError(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_BUCKET is required when "
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND=s3"
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
    backend: str
    bucket: str | None
    key: str
    uri: str
    format: str
    uncompressed_bytes: int
    compressed_bytes: int
    uncompressed_sha256: str
    compressed_sha256: str
    metadata: dict[str, str]


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


def _object_key_for_hash(prefix: str, tx_hash: str) -> str:
    normalized = tx_hash.lower().removeprefix("0x")
    shard = normalized[:2] if len(normalized) >= 2 else "unknown"
    filename = f"{tx_hash}.contract_snapshot.json.gz"
    clean_prefix = prefix.strip("/")
    if not clean_prefix:
        return f"v1/{shard}/{filename}"
    return f"{clean_prefix}/v1/{shard}/{filename}"


def _archive_body(candidate: SnapshotCandidate) -> tuple[bytes, bytes, str, str]:
    raw = candidate.snapshot_json.encode("utf-8")
    body = gzip.compress(raw, compresslevel=6, mtime=0)
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    compressed_sha256 = hashlib.sha256(body).hexdigest()
    return raw, body, raw_sha256, compressed_sha256


def _archive_metadata(
    candidate: SnapshotCandidate,
    *,
    raw_sha256: str,
    compressed_sha256: str,
    raw_bytes: int,
    compressed_bytes: int,
) -> dict[str, str]:
    return {
        "schema-version": "1",
        "archive-format": ARCHIVE_FORMAT,
        "tx-hash": candidate.tx_hash,
        "tx-status": candidate.status,
        "snapshot-sha256": raw_sha256,
        "compressed-sha256": compressed_sha256,
        "snapshot-bytes": str(raw_bytes),
        "compressed-bytes": str(compressed_bytes),
    }


class FileSnapshotArchiveWriter:
    backend = "file"

    def __init__(self, *, base_dir: str | Path, prefix: str) -> None:
        self.base_dir = Path(base_dir)
        self.prefix = prefix.strip("/")

    @classmethod
    def from_config(cls, config: TerminalSnapshotPrunerConfig):
        return cls(base_dir=config.file_dir, prefix=DEFAULT_ARCHIVE_PREFIX)

    def key_for_hash(self, tx_hash: str) -> str:
        return _object_key_for_hash(self.prefix, tx_hash)

    def archive(self, candidate: SnapshotCandidate) -> ArchiveResult:
        raw, body, raw_sha256, compressed_sha256 = _archive_body(candidate)
        metadata = _archive_metadata(
            candidate,
            raw_sha256=raw_sha256,
            compressed_sha256=compressed_sha256,
            raw_bytes=len(raw),
            compressed_bytes=len(body),
        )
        key = self.key_for_hash(candidate.tx_hash)
        destination = self.base_dir / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = destination.with_name(
            f".{destination.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        )
        tmp_path.write_bytes(body)
        tmp_path.replace(destination)

        return ArchiveResult(
            backend=self.backend,
            bucket=None,
            key=key,
            uri=f"file://{destination.resolve()}",
            format=ARCHIVE_FORMAT,
            uncompressed_bytes=len(raw),
            compressed_bytes=len(body),
            uncompressed_sha256=raw_sha256,
            compressed_sha256=compressed_sha256,
            metadata=metadata,
        )


class GCSSnapshotArchiveWriter:
    backend = "gcs"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        storage_class: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.storage_class = storage_class
        self._client = client

    @classmethod
    def from_config(cls, config: TerminalSnapshotPrunerConfig):
        if not config.gcs_bucket:
            raise RuntimeError("GCS bucket is required for snapshot archiving")
        return cls(
            bucket=config.gcs_bucket,
            prefix=config.gcs_prefix,
            storage_class=config.gcs_storage_class,
        )

    @property
    def client(self):
        if self._client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:  # pragma: no cover - depends on image deps
                raise RuntimeError(
                    "google-cloud-storage is required for GCS contract snapshot "
                    "archiving"
                ) from exc
            self._client = storage.Client()
        return self._client

    def key_for_hash(self, tx_hash: str) -> str:
        return _object_key_for_hash(self.prefix, tx_hash)

    def archive(self, candidate: SnapshotCandidate) -> ArchiveResult:
        raw, body, raw_sha256, compressed_sha256 = _archive_body(candidate)
        metadata = _archive_metadata(
            candidate,
            raw_sha256=raw_sha256,
            compressed_sha256=compressed_sha256,
            raw_bytes=len(raw),
            compressed_bytes=len(body),
        )
        key = self.key_for_hash(candidate.tx_hash)
        bucket = self.client.bucket(self.bucket)
        blob = bucket.blob(key)
        blob.content_encoding = "gzip"
        blob.metadata = metadata
        if self.storage_class:
            blob.storage_class = self.storage_class
        blob.upload_from_string(body, content_type="application/json")

        return ArchiveResult(
            backend=self.backend,
            bucket=self.bucket,
            key=key,
            uri=f"gs://{self.bucket}/{key}",
            format=ARCHIVE_FORMAT,
            uncompressed_bytes=len(raw),
            compressed_bytes=len(body),
            uncompressed_sha256=raw_sha256,
            compressed_sha256=compressed_sha256,
            metadata=metadata,
        )


class S3SnapshotArchiveWriter:
    backend = "s3"

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
        return _object_key_for_hash(self.prefix, tx_hash)

    def archive(self, candidate: SnapshotCandidate) -> ArchiveResult:
        raw, body, raw_sha256, compressed_sha256 = _archive_body(candidate)
        metadata = _archive_metadata(
            candidate,
            raw_sha256=raw_sha256,
            compressed_sha256=compressed_sha256,
            raw_bytes=len(raw),
            compressed_bytes=len(body),
        )
        key = self.key_for_hash(candidate.tx_hash)
        compressed_digest = bytes.fromhex(compressed_sha256)

        put_kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentEncoding": "gzip",
            "ContentType": "application/json",
            "ChecksumSHA256": base64.b64encode(compressed_digest).decode("ascii"),
            "Metadata": metadata,
        }
        if self.storage_class:
            put_kwargs["StorageClass"] = self.storage_class
        if self.sse:
            put_kwargs["ServerSideEncryption"] = self.sse
        if self.kms_key_id:
            put_kwargs["SSEKMSKeyId"] = self.kms_key_id

        self.client.put_object(**put_kwargs)

        return ArchiveResult(
            backend=self.backend,
            bucket=self.bucket,
            key=key,
            uri=f"s3://{self.bucket}/{key}",
            format=ARCHIVE_FORMAT,
            uncompressed_bytes=len(raw),
            compressed_bytes=len(body),
            uncompressed_sha256=raw_sha256,
            compressed_sha256=compressed_sha256,
            metadata=metadata,
        )


def build_snapshot_archive_writer(
    config: TerminalSnapshotPrunerConfig,
) -> SnapshotArchiveWriter:
    backend = config.archive_backend.lower()
    if backend == "file":
        return FileSnapshotArchiveWriter.from_config(config)
    if backend == "gcs":
        return GCSSnapshotArchiveWriter.from_config(config)
    if backend == "s3":
        return S3SnapshotArchiveWriter.from_config(config)
    raise RuntimeError(f"Unsupported contract snapshot archive backend: {backend}")


class SnapshotArchiveReader:
    def __init__(
        self,
        *,
        file_dir: str | Path | None = None,
        s3_region: str | None = None,
        s3_client: Any | None = None,
        gcs_client: Any | None = None,
    ) -> None:
        self.file_dir = Path(file_dir) if file_dir else None
        self.s3_region = s3_region
        self._s3_client = s3_client
        self._gcs_client = gcs_client

    @classmethod
    def from_environment(cls) -> "SnapshotArchiveReader":
        return cls(
            file_dir=os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_FILE_DIR")
            or DEFAULT_FILE_ARCHIVE_DIR,
            s3_region=os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or os.getenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_REGION")
            or None,
        )

    @property
    def s3_client(self):
        if self._s3_client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - depends on image deps
                raise RuntimeError(
                    "boto3 is required for S3 contract snapshot retrieval"
                ) from exc
            self._s3_client = boto3.client("s3", region_name=self.s3_region)
        return self._s3_client

    @property
    def gcs_client(self):
        if self._gcs_client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:  # pragma: no cover - depends on image deps
                raise RuntimeError(
                    "google-cloud-storage is required for GCS contract snapshot "
                    "retrieval"
                ) from exc
            self._gcs_client = storage.Client()
        return self._gcs_client

    def load_snapshot(self, session: Session, tx_hash: str) -> dict | list | None:
        row = (
            session.execute(
                text(
                    """
                    SELECT
                        tx_hash,
                        backend,
                        bucket,
                        object_key,
                        uri,
                        format,
                        snapshot_sha256,
                        compressed_sha256,
                        snapshot_bytes,
                        compressed_bytes
                    FROM transaction_snapshot_archives
                    WHERE tx_hash = :hash
                      AND archive_status IN ('archived', 'pruned')
                    ORDER BY archived_at DESC
                    LIMIT 1
                    """
                ),
                {"hash": tx_hash},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        if row["format"] != ARCHIVE_FORMAT:
            raise RuntimeError(
                f"Unsupported contract snapshot archive format: {row['format']}"
            )

        body = self._download(row)
        compressed_sha256 = hashlib.sha256(body).hexdigest()
        if row["compressed_sha256"] and compressed_sha256 != row["compressed_sha256"]:
            raise RuntimeError(
                f"Archived contract snapshot checksum mismatch for {tx_hash}"
            )

        raw = gzip.decompress(body)
        snapshot_sha256 = hashlib.sha256(raw).hexdigest()
        if row["snapshot_sha256"] and snapshot_sha256 != row["snapshot_sha256"]:
            raise RuntimeError(
                f"Archived contract snapshot content checksum mismatch for {tx_hash}"
            )
        return json.loads(raw.decode("utf-8"))

    def _download(self, row: dict[str, Any]) -> bytes:
        backend = row["backend"].lower()
        if backend == "file":
            return self._download_file(row)
        if backend == "gcs":
            return self._download_gcs(row)
        if backend == "s3":
            return self._download_s3(row)
        raise RuntimeError(f"Unsupported contract snapshot archive backend: {backend}")

    def _download_file(self, row: dict[str, Any]) -> bytes:
        uri = row.get("uri")
        if uri and uri.startswith("file://"):
            path = Path(uri[7:])
        elif self.file_dir is not None:
            path = self.file_dir / row["object_key"]
        else:
            path = Path(row["object_key"])
        return path.read_bytes()

    def _download_gcs(self, row: dict[str, Any]) -> bytes:
        bucket = row["bucket"]
        if not bucket:
            raise RuntimeError("GCS archive row is missing bucket")
        return (
            self.gcs_client.bucket(bucket).blob(row["object_key"]).download_as_bytes()
        )

    def _download_s3(self, row: dict[str, Any]) -> bytes:
        bucket = row["bucket"]
        if not bucket:
            raise RuntimeError("S3 archive row is missing bucket")
        response = self.s3_client.get_object(Bucket=bucket, Key=row["object_key"])
        return response["Body"].read()


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
            self.archive_writer = build_snapshot_archive_writer(self.config)
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

    def _record_archive(
        self,
        session: Session,
        candidate: SnapshotCandidate,
        archive_result: ArchiveResult,
    ) -> None:
        session.execute(
            text(
                """
                INSERT INTO transaction_snapshot_archives (
                    tx_hash,
                    backend,
                    bucket,
                    object_key,
                    uri,
                    format,
                    snapshot_sha256,
                    compressed_sha256,
                    snapshot_bytes,
                    compressed_bytes,
                    archive_status,
                    archived_at,
                    object_metadata
                )
                VALUES (
                    :tx_hash,
                    :backend,
                    :bucket,
                    :object_key,
                    :uri,
                    :format,
                    :snapshot_sha256,
                    :compressed_sha256,
                    :snapshot_bytes,
                    :compressed_bytes,
                    'archived',
                    CURRENT_TIMESTAMP,
                    CAST(:object_metadata AS jsonb)
                )
                ON CONFLICT (tx_hash) DO UPDATE SET
                    backend = EXCLUDED.backend,
                    bucket = EXCLUDED.bucket,
                    object_key = EXCLUDED.object_key,
                    uri = EXCLUDED.uri,
                    format = EXCLUDED.format,
                    snapshot_sha256 = EXCLUDED.snapshot_sha256,
                    compressed_sha256 = EXCLUDED.compressed_sha256,
                    snapshot_bytes = EXCLUDED.snapshot_bytes,
                    compressed_bytes = EXCLUDED.compressed_bytes,
                    archive_status = 'archived',
                    archived_at = CURRENT_TIMESTAMP,
                    pruned_at = NULL,
                    object_metadata = EXCLUDED.object_metadata
                """
            ),
            {
                "tx_hash": candidate.tx_hash,
                "backend": archive_result.backend,
                "bucket": archive_result.bucket,
                "object_key": archive_result.key,
                "uri": archive_result.uri,
                "format": archive_result.format,
                "snapshot_sha256": archive_result.uncompressed_sha256,
                "compressed_sha256": archive_result.compressed_sha256,
                "snapshot_bytes": archive_result.uncompressed_bytes,
                "compressed_bytes": archive_result.compressed_bytes,
                "object_metadata": json.dumps(archive_result.metadata),
            },
        )

    def _mark_archive_pruned(self, session: Session, tx_hash: str) -> None:
        session.execute(
            text(
                """
                UPDATE transaction_snapshot_archives
                SET archive_status = 'pruned',
                    pruned_at = CURRENT_TIMESTAMP
                WHERE tx_hash = :hash
                """
            ),
            {"hash": tx_hash},
        )

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
                    self._record_archive(session, candidate, archive_result)
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
                rowcount = result.rowcount or 0
                pruned += rowcount
                if rowcount and writer is not None:
                    self._mark_archive_pruned(session, candidate.tx_hash)

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
        "(batch_size=%s retention_hours=%s archive_enabled=%s "
        "archive_backend=%s dry_run=%s)",
        config.batch_size,
        config.retention_hours,
        config.archive_enabled,
        config.archive_backend,
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
