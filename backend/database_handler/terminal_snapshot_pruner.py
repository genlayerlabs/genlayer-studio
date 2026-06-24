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
    verify_archive: bool = True
    allow_lossy_prune: bool = False
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
            verify_archive=_env_bool(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_VERIFY_ARCHIVE", default=True
            ),
            allow_lossy_prune=_env_bool(
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_ALLOW_LOSSY_PRUNE"
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

    def _validate_archive_backend(self) -> None:
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

    def validate_for_archive(self) -> None:
        if self.dry_run:
            return
        if not self.archive_enabled:
            raise RuntimeError(
                "Archive phase requires "
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED=true"
            )
        self._validate_archive_backend()

    def validate_for_verify(self) -> None:
        if self.dry_run:
            return
        self._validate_archive_backend()

    def validate_for_run(self) -> None:
        if self.dry_run:
            return
        if not self.archive_enabled:
            if self.allow_lossy_prune:
                return
            raise RuntimeError(
                "Refusing to prune terminal contract snapshots without archiving. "
                "Set STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED=true, or set "
                "STUDIO_CONTRACT_SNAPSHOT_PRUNER_ALLOW_LOSSY_PRUNE=true to make "
                "the data-loss mode explicit."
            )
        self._validate_archive_backend()


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
class SnapshotArchiveRecord:
    tx_hash: str
    backend: str
    bucket: str | None
    object_key: str
    uri: str
    format: str
    snapshot_sha256: str
    compressed_sha256: str
    snapshot_bytes: int
    compressed_bytes: int

    def to_archive_result(self) -> ArchiveResult:
        return ArchiveResult(
            backend=self.backend,
            bucket=self.bucket,
            key=self.object_key,
            uri=self.uri,
            format=self.format,
            uncompressed_bytes=self.snapshot_bytes,
            compressed_bytes=self.compressed_bytes,
            uncompressed_sha256=self.snapshot_sha256,
            compressed_sha256=self.compressed_sha256,
            metadata={"tx-hash": self.tx_hash},
        )


@dataclass(frozen=True)
class PruneBatchResult:
    candidates: int = 0
    archived: int = 0
    verified: int = 0
    pruned: int = 0
    logical_bytes: int = 0
    compressed_bytes: int = 0
    dry_run: bool = False


class SnapshotArchiveWriter(Protocol):
    def archive(self, candidate: SnapshotCandidate) -> ArchiveResult: ...

    def verify(self, archive_result: ArchiveResult) -> None: ...


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


def _decode_verified_archive_body(
    *,
    body: bytes,
    tx_hash: str,
    archive_format: str,
    uncompressed_sha256: str | None,
    compressed_sha256: str | None,
) -> dict | list:
    if archive_format != ARCHIVE_FORMAT:
        raise RuntimeError(
            f"Unsupported contract snapshot archive format: {archive_format}"
        )

    actual_compressed_sha256 = hashlib.sha256(body).hexdigest()
    if compressed_sha256 and actual_compressed_sha256 != compressed_sha256:
        raise RuntimeError(
            f"Archived contract snapshot checksum mismatch for {tx_hash}"
        )

    raw = gzip.decompress(body)
    actual_uncompressed_sha256 = hashlib.sha256(raw).hexdigest()
    if uncompressed_sha256 and actual_uncompressed_sha256 != uncompressed_sha256:
        raise RuntimeError(
            f"Archived contract snapshot content checksum mismatch for {tx_hash}"
        )
    return json.loads(raw.decode("utf-8"))


def _verify_archive_result_body(archive_result: ArchiveResult, body: bytes) -> None:
    _decode_verified_archive_body(
        body=body,
        tx_hash=archive_result.metadata.get("tx-hash", archive_result.key),
        archive_format=archive_result.format,
        uncompressed_sha256=archive_result.uncompressed_sha256,
        compressed_sha256=archive_result.compressed_sha256,
    )


def _is_precondition_failed(exc: Exception) -> bool:
    if exc.__class__.__name__ == "PreconditionFailed":
        return True
    return getattr(exc, "code", None) == 412


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

    def verify(self, archive_result: ArchiveResult) -> None:
        uri = archive_result.uri
        if uri.startswith("file://"):
            path = Path(uri[7:])
        else:
            path = self.base_dir / archive_result.key
        _verify_archive_result_body(archive_result, path.read_bytes())


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
        archive_result = ArchiveResult(
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
        try:
            blob.upload_from_string(
                body,
                content_type="application/json",
                if_generation_match=0,
            )
        except Exception as exc:
            if not _is_precondition_failed(exc):
                raise
            self._verify_existing_object(bucket, key, archive_result, metadata)

        return archive_result

    def _verify_existing_object(
        self,
        bucket,
        key: str,
        archive_result: ArchiveResult,
        expected_metadata: dict[str, str],
    ) -> None:
        existing_blob = bucket.get_blob(key)
        if existing_blob is None:
            raise RuntimeError(
                f"GCS archive object already exists but could not be read: {key}"
            )

        body = existing_blob.download_as_bytes(raw_download=True)
        _verify_archive_result_body(archive_result, body)

        actual_metadata = existing_blob.metadata or {}
        for metadata_key, expected_value in expected_metadata.items():
            actual_value = actual_metadata.get(metadata_key)
            if actual_value != expected_value:
                raise RuntimeError(
                    "Existing GCS archive metadata mismatch for "
                    f"{archive_result.key}: {metadata_key}"
                )

    def verify(self, archive_result: ArchiveResult) -> None:
        if not archive_result.bucket:
            raise RuntimeError("GCS archive result is missing bucket")
        body = (
            self.client.bucket(archive_result.bucket)
            .blob(archive_result.key)
            .download_as_bytes(raw_download=True)
        )
        _verify_archive_result_body(archive_result, body)


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

    def verify(self, archive_result: ArchiveResult) -> None:
        if not archive_result.bucket:
            raise RuntimeError("S3 archive result is missing bucket")
        response = self.client.get_object(
            Bucket=archive_result.bucket,
            Key=archive_result.key,
        )
        _verify_archive_result_body(archive_result, response["Body"].read())


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
                      AND (
                          archive_status = 'pruned'
                          OR verified_at IS NOT NULL
                      )
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
        body = self._download(row)
        return _decode_verified_archive_body(
            body=body,
            tx_hash=tx_hash,
            archive_format=row["format"],
            uncompressed_sha256=row["snapshot_sha256"],
            compressed_sha256=row["compressed_sha256"],
        )

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
            self.gcs_client.bucket(bucket)
            .blob(row["object_key"])
            .download_as_bytes(raw_download=True)
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

    def _fetch_archive_candidates(self, session: Session) -> list[SnapshotCandidate]:
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
                      AND NOT EXISTS (
                          SELECT 1
                          FROM transaction_snapshot_archives archives
                          WHERE archives.tx_hash = transactions.hash
                            AND archives.archive_status IN ('archived', 'pruned')
                      )
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

    def _fetch_unverified_archives(
        self, session: Session
    ) -> list[SnapshotArchiveRecord]:
        rows = (
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
                    WHERE archive_status = 'archived'
                      AND verified_at IS NULL
                    ORDER BY archived_at ASC, tx_hash ASC
                    LIMIT :batch_size
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"batch_size": self.config.batch_size},
            )
            .mappings()
            .all()
        )

        return [
            SnapshotArchiveRecord(
                tx_hash=row["tx_hash"],
                backend=row["backend"],
                bucket=row["bucket"],
                object_key=row["object_key"],
                uri=row["uri"],
                format=row["format"],
                snapshot_sha256=row["snapshot_sha256"],
                compressed_sha256=row["compressed_sha256"],
                snapshot_bytes=int(row["snapshot_bytes"] or 0),
                compressed_bytes=int(row["compressed_bytes"] or 0),
            )
            for row in rows
        ]

    def _fetch_verified_prune_candidates(
        self, session: Session
    ) -> list[SnapshotArchiveRecord]:
        rows = (
            session.execute(
                text(
                    """
                    SELECT
                        archives.tx_hash,
                        archives.backend,
                        archives.bucket,
                        archives.object_key,
                        archives.uri,
                        archives.format,
                        archives.snapshot_sha256,
                        archives.compressed_sha256,
                        pg_column_size(transactions.contract_snapshot)
                            AS snapshot_bytes,
                        archives.compressed_bytes
                    FROM transaction_snapshot_archives archives
                    JOIN transactions ON transactions.hash = archives.tx_hash
                    WHERE archives.archive_status = 'archived'
                      AND archives.verified_at IS NOT NULL
                      AND transactions.contract_snapshot IS NOT NULL
                      AND transactions.status IN ('FINALIZED', 'CANCELED')
                      AND transactions.created_at < :cutoff
                    ORDER BY archives.verified_at ASC,
                             transactions.created_at ASC,
                             archives.tx_hash ASC
                    LIMIT :batch_size
                    FOR UPDATE OF archives, transactions SKIP LOCKED
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
            SnapshotArchiveRecord(
                tx_hash=row["tx_hash"],
                backend=row["backend"],
                bucket=row["bucket"],
                object_key=row["object_key"],
                uri=row["uri"],
                format=row["format"],
                snapshot_sha256=row["snapshot_sha256"],
                compressed_sha256=row["compressed_sha256"],
                snapshot_bytes=int(row["snapshot_bytes"] or 0),
                compressed_bytes=int(row["compressed_bytes"] or 0),
            )
            for row in rows
        ]

    def _record_archive(
        self,
        session: Session,
        candidate: SnapshotCandidate,
        archive_result: ArchiveResult,
        *,
        verified: bool = False,
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
                    verified_at,
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
                    CASE WHEN :verified THEN CURRENT_TIMESTAMP ELSE NULL END,
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
                    verified_at = EXCLUDED.verified_at,
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
                "verified": verified,
                "object_metadata": json.dumps(archive_result.metadata),
            },
        )

    def _mark_archive_verified(self, session: Session, tx_hash: str) -> None:
        session.execute(
            text(
                """
                UPDATE transaction_snapshot_archives
                SET verified_at = CURRENT_TIMESTAMP
                WHERE tx_hash = :hash
                  AND archive_status = 'archived'
                """
            ),
            {"hash": tx_hash},
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

    def archive_once(self, *, verify_inline: bool = False) -> PruneBatchResult:
        self.config.validate_for_archive()
        session = self.get_session()
        archived = 0
        verified = 0
        logical_bytes = 0
        compressed_bytes = 0
        try:
            candidates = self._fetch_archive_candidates(session)
            if not candidates:
                session.rollback()
                return PruneBatchResult(dry_run=self.config.dry_run)

            logical_bytes = sum(candidate.snapshot_bytes for candidate in candidates)
            if self.config.dry_run:
                session.rollback()
                return PruneBatchResult(
                    candidates=len(candidates),
                    logical_bytes=logical_bytes,
                    dry_run=True,
                )

            writer = self._archive_writer()
            for candidate in candidates:
                archive_result = writer.archive(candidate)
                if verify_inline:
                    writer.verify(archive_result)
                    verified += 1
                self._record_archive(
                    session,
                    candidate,
                    archive_result,
                    verified=verify_inline,
                )
                archived += 1
                compressed_bytes += archive_result.compressed_bytes

            session.commit()
            return PruneBatchResult(
                candidates=len(candidates),
                archived=archived,
                verified=verified,
                logical_bytes=logical_bytes,
                compressed_bytes=compressed_bytes,
                dry_run=False,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def verify_archives_once(self) -> PruneBatchResult:
        self.config.validate_for_verify()
        session = self.get_session()
        try:
            archives = self._fetch_unverified_archives(session)
            if not archives:
                session.rollback()
                return PruneBatchResult(dry_run=self.config.dry_run)

            logical_bytes = sum(archive.snapshot_bytes for archive in archives)
            compressed_bytes = sum(archive.compressed_bytes for archive in archives)
            if self.config.dry_run:
                session.rollback()
                return PruneBatchResult(
                    candidates=len(archives),
                    logical_bytes=logical_bytes,
                    compressed_bytes=compressed_bytes,
                    dry_run=True,
                )

            writer = self._archive_writer()
            verified = 0
            for archive in archives:
                writer.verify(archive.to_archive_result())
                self._mark_archive_verified(session, archive.tx_hash)
                verified += 1

            session.commit()
            return PruneBatchResult(
                candidates=len(archives),
                verified=verified,
                logical_bytes=logical_bytes,
                compressed_bytes=compressed_bytes,
                dry_run=False,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def prune_verified_once(self) -> PruneBatchResult:
        session = self.get_session()
        pruned = 0
        try:
            archives = self._fetch_verified_prune_candidates(session)
            if not archives:
                session.rollback()
                return PruneBatchResult(dry_run=self.config.dry_run)

            logical_bytes = sum(archive.snapshot_bytes for archive in archives)
            compressed_bytes = sum(archive.compressed_bytes for archive in archives)
            if self.config.dry_run:
                session.rollback()
                return PruneBatchResult(
                    candidates=len(archives),
                    logical_bytes=logical_bytes,
                    compressed_bytes=compressed_bytes,
                    dry_run=True,
                )

            for archive in archives:
                result = session.execute(
                    text(
                        """
                        UPDATE transactions
                        SET contract_snapshot = NULL
                        WHERE hash = :hash
                          AND contract_snapshot IS NOT NULL
                        """
                    ),
                    {"hash": archive.tx_hash},
                )
                rowcount = result.rowcount or 0
                pruned += rowcount
                if rowcount:
                    self._mark_archive_pruned(session, archive.tx_hash)

            session.commit()
            return PruneBatchResult(
                candidates=len(archives),
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

    def prune_once(self) -> PruneBatchResult:
        self.config.validate_for_run()
        session = self.get_session()
        archived = 0
        verified = 0
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
                    if self.config.verify_archive:
                        writer.verify(archive_result)
                        verified += 1
                    self._record_archive(
                        session,
                        candidate,
                        archive_result,
                        verified=self.config.verify_archive,
                    )
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
                verified=verified,
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
                    "candidates=%s archived=%s verified=%s pruned=%s "
                    "logical_bytes=%s compressed_bytes=%s dry_run=%s "
                    "elapsed=%.2fs",
                    result.candidates,
                    result.archived,
                    result.verified,
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
