import asyncio
import base64
import gzip
import hashlib
import sys
import threading
from datetime import datetime, timezone

import pytest

from backend.database_handler import prune_terminal_snapshots as prune_cli
from backend.database_handler import terminal_snapshot_pruner as pruner_mod
from backend.database_handler.terminal_snapshot_pruner import (
    ARCHIVE_FORMAT,
    ArchiveResult,
    FileSnapshotArchiveWriter,
    GCSSnapshotArchiveWriter,
    PruneBatchResult,
    S3SnapshotArchiveWriter,
    SnapshotCandidate,
    SnapshotArchiveReader,
    TerminalSnapshotPruner,
    TerminalSnapshotPrunerConfig,
    build_snapshot_archive_writer,
    run_terminal_snapshot_pruner_loop,
    snapshot_archive_read_through_enabled,
)


class FakeS3Client:
    def __init__(self):
        self.calls = []
        self.objects = {}

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}


class FakeBody:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data


class FakeGCSBlob:
    def __init__(self, name):
        self.name = name
        self.content_encoding = None
        self.metadata = None
        self.storage_class = None
        self.uploads = []
        self.download_calls = []

    def upload_from_string(self, data, content_type):
        self.uploads.append({"data": data, "content_type": content_type})
        self.data = data

    def download_as_bytes(self, raw_download=False):
        self.download_calls.append(raw_download)
        if self.content_encoding == "gzip" and not raw_download:
            return gzip.decompress(self.data)
        return self.data


class FakeGCSBucket:
    def __init__(self, name):
        self.name = name
        self.blobs = {}

    def blob(self, key):
        return self.blobs.setdefault(key, FakeGCSBlob(key))


class FakeGCSClient:
    def __init__(self):
        self.buckets = {}

    def bucket(self, name):
        bucket = self.buckets.setdefault(name, FakeGCSBucket(name))
        return bucket


class FakeMappingsResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeUpdateResult:
    rowcount = 1


class FakeSession:
    def __init__(
        self,
        rows,
        archive_rows=None,
        fail_on_archive_insert=False,
        fail_on_transaction_update=False,
    ):
        self.rows = rows
        self.archive_rows = archive_rows or []
        self.archive_inserts = []
        self.pruned_archive_hashes = []
        self.updated_hashes = []
        self.fail_on_archive_insert = fail_on_archive_insert
        self.fail_on_transaction_update = fail_on_transaction_update
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM transaction_snapshot_archives" in sql:
            return FakeMappingsResult(self.archive_rows)
        if "INSERT INTO transaction_snapshot_archives" in sql:
            if self.fail_on_archive_insert:
                raise RuntimeError("archive index insert failed")
            self.archive_inserts.append(params)
            return FakeUpdateResult()
        if "UPDATE transaction_snapshot_archives" in sql:
            self.pruned_archive_hashes.append(params["hash"])
            return FakeUpdateResult()
        if "SELECT" in sql:
            return FakeMappingsResult(self.rows)
        if "UPDATE transactions" in sql:
            if self.fail_on_transaction_update:
                raise RuntimeError("transaction prune failed")
            self.updated_hashes.append(params["hash"])
            return FakeUpdateResult()
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class RecordingArchiveWriter:
    def __init__(self, fail=False, verify_fail=False):
        self.fail = fail
        self.verify_fail = verify_fail
        self.archived = []
        self.verified = []

    def archive(self, candidate):
        if self.fail:
            raise RuntimeError("archive failed")
        self.archived.append(candidate.tx_hash)
        return ArchiveResult(
            backend="file",
            bucket=None,
            key=f"studio/v1/{candidate.tx_hash}.json.gz",
            uri=f"file:///tmp/{candidate.tx_hash}.json.gz",
            format=ARCHIVE_FORMAT,
            uncompressed_bytes=candidate.snapshot_bytes,
            compressed_bytes=42,
            uncompressed_sha256="0" * 64,
            compressed_sha256="1" * 64,
            metadata={"tx-hash": candidate.tx_hash},
        )

    def verify(self, archive_result):
        if self.verify_fail:
            raise RuntimeError("archive verification failed")
        self.verified.append(archive_result.key)


def _candidate_row(tx_hash="0xabc"):
    snapshot_json = '{"state":{"accepted":{"a":"b"},"finalized":{"a":"b"}}}'
    return {
        "hash": tx_hash,
        "status": "FINALIZED",
        "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "snapshot_bytes": len(snapshot_json),
        "snapshot_json": snapshot_json,
    }


def _archive_row(result):
    return {
        "tx_hash": result.metadata["tx-hash"],
        "backend": result.backend,
        "bucket": result.bucket,
        "object_key": result.key,
        "uri": result.uri,
        "format": result.format,
        "snapshot_sha256": result.uncompressed_sha256,
        "compressed_sha256": result.compressed_sha256,
        "snapshot_bytes": result.uncompressed_bytes,
        "compressed_bytes": result.compressed_bytes,
    }


def test_s3_archive_writer_compresses_snapshot_with_checksum_and_metadata():
    client = FakeS3Client()
    writer = S3SnapshotArchiveWriter(
        bucket="archive-bucket",
        prefix="studio/rally",
        storage_class="GLACIER_IR",
        sse="aws:kms",
        kms_key_id="alias/studio-archive",
        client=client,
    )
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )

    result = writer.archive(candidate)

    assert result.bucket == "archive-bucket"
    assert result.key == "studio/rally/v1/ab/0xABCDEF.contract_snapshot.json.gz"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["Bucket"] == "archive-bucket"
    assert call["Key"] == result.key
    assert call["StorageClass"] == "GLACIER_IR"
    assert call["ServerSideEncryption"] == "aws:kms"
    assert call["SSEKMSKeyId"] == "alias/studio-archive"
    assert call["ContentEncoding"] == "gzip"
    assert gzip.decompress(call["Body"]) == b'{"x":1}'
    expected_digest = hashlib.sha256(call["Body"]).digest()
    assert call["ChecksumSHA256"] == base64.b64encode(expected_digest).decode("ascii")
    assert call["Metadata"]["tx-hash"] == "0xABCDEF"
    assert call["Metadata"]["snapshot-sha256"] == hashlib.sha256(b'{"x":1}').hexdigest()
    writer.verify(result)


def test_archive_writers_handle_minimal_cloud_options():
    s3_client = FakeS3Client()
    s3_writer = S3SnapshotArchiveWriter(
        bucket="archive-bucket",
        prefix="studio/rally",
        client=s3_client,
    )
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )

    s3_result = s3_writer.archive(candidate)

    s3_call = s3_client.calls[0]
    assert "StorageClass" not in s3_call
    assert "ServerSideEncryption" not in s3_call
    assert "SSEKMSKeyId" not in s3_call
    assert s3_result.uri == f"s3://archive-bucket/{s3_result.key}"

    gcs_client = FakeGCSClient()
    gcs_writer = GCSSnapshotArchiveWriter(
        bucket="archive-bucket",
        prefix="studio/rally",
        client=gcs_client,
    )

    gcs_result = gcs_writer.archive(candidate)

    blob = gcs_client.buckets["archive-bucket"].blobs[gcs_result.key]
    assert blob.storage_class is None
    assert gcs_result.uri == f"gs://archive-bucket/{gcs_result.key}"


def test_file_archive_writer_writes_snapshot_and_reader_loads_it(tmp_path):
    writer = FileSnapshotArchiveWriter(base_dir=tmp_path, prefix="studio/rally")
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )

    result = writer.archive(candidate)

    archive_path = tmp_path / result.key
    assert archive_path.exists()
    assert gzip.decompress(archive_path.read_bytes()) == b'{"x":1}'
    assert result.backend == "file"
    assert result.bucket is None
    assert result.uri.startswith("file://")

    session = FakeSession(
        [],
        archive_rows=[
            {
                "tx_hash": candidate.tx_hash,
                "backend": result.backend,
                "bucket": result.bucket,
                "object_key": result.key,
                "uri": result.uri,
                "format": result.format,
                "snapshot_sha256": result.uncompressed_sha256,
                "compressed_sha256": result.compressed_sha256,
                "snapshot_bytes": result.uncompressed_bytes,
                "compressed_bytes": result.compressed_bytes,
            }
        ],
    )
    reader = SnapshotArchiveReader(file_dir=tmp_path)

    assert reader.load_snapshot(session, candidate.tx_hash) == {"x": 1}


def test_archive_reader_loads_cloud_backends():
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )
    s3_client = FakeS3Client()
    s3_result = S3SnapshotArchiveWriter(
        bucket="archive-bucket",
        prefix="studio/rally",
        client=s3_client,
    ).archive(candidate)
    s3_session = FakeSession([], archive_rows=[_archive_row(s3_result)])

    assert SnapshotArchiveReader(s3_client=s3_client).load_snapshot(
        s3_session, candidate.tx_hash
    ) == {"x": 1}

    gcs_client = FakeGCSClient()
    gcs_result = GCSSnapshotArchiveWriter(
        bucket="archive-bucket",
        prefix="studio/rally",
        client=gcs_client,
    ).archive(candidate)
    gcs_session = FakeSession([], archive_rows=[_archive_row(gcs_result)])

    assert SnapshotArchiveReader(gcs_client=gcs_client).load_snapshot(
        gcs_session, candidate.tx_hash
    ) == {"x": 1}
    gcs_blob = gcs_client.buckets["archive-bucket"].blobs[gcs_result.key]
    assert gcs_blob.download_calls == [True]


def test_archive_reader_file_download_fallbacks(tmp_path):
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )
    result = FileSnapshotArchiveWriter(
        base_dir=tmp_path, prefix="studio/rally"
    ).archive(candidate)
    row = _archive_row(result)
    row["uri"] = None

    assert SnapshotArchiveReader(file_dir=tmp_path).load_snapshot(
        FakeSession([], archive_rows=[row]), candidate.tx_hash
    ) == {"x": 1}

    absolute_path = tmp_path / "absolute.contract_snapshot.json.gz"
    absolute_path.write_bytes((tmp_path / result.key).read_bytes())
    absolute_row = _archive_row(result)
    absolute_row["uri"] = None
    absolute_row["object_key"] = str(absolute_path)

    assert SnapshotArchiveReader(file_dir=None).load_snapshot(
        FakeSession([], archive_rows=[absolute_row]), candidate.tx_hash
    ) == {"x": 1}


def test_archive_reader_returns_none_without_archive_row():
    reader = SnapshotArchiveReader()

    assert reader.load_snapshot(FakeSession([], archive_rows=[]), "0xmissing") is None


def test_archive_reader_rejects_checksum_mismatch(tmp_path):
    writer = FileSnapshotArchiveWriter(base_dir=tmp_path, prefix="studio/rally")
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )
    result = writer.archive(candidate)
    archive_path = tmp_path / result.key
    archive_path.write_bytes(gzip.compress(b'{"x":2}', compresslevel=6, mtime=0))
    session = FakeSession(
        [],
        archive_rows=[
            {
                "tx_hash": candidate.tx_hash,
                "backend": result.backend,
                "bucket": result.bucket,
                "object_key": result.key,
                "uri": result.uri,
                "format": result.format,
                "snapshot_sha256": result.uncompressed_sha256,
                "compressed_sha256": result.compressed_sha256,
                "snapshot_bytes": result.uncompressed_bytes,
                "compressed_bytes": result.compressed_bytes,
            }
        ],
    )
    reader = SnapshotArchiveReader(file_dir=tmp_path)

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        reader.load_snapshot(session, candidate.tx_hash)


def test_archive_reader_rejects_uncompressed_checksum_mismatch(tmp_path):
    writer = FileSnapshotArchiveWriter(base_dir=tmp_path, prefix="studio/rally")
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )
    result = writer.archive(candidate)
    row = _archive_row(result)
    row["snapshot_sha256"] = "0" * 64
    session = FakeSession([], archive_rows=[row])

    with pytest.raises(RuntimeError, match="content checksum mismatch"):
        SnapshotArchiveReader(file_dir=tmp_path).load_snapshot(
            session, candidate.tx_hash
        )


def test_archive_reader_rejects_unsupported_archive_format(tmp_path):
    writer = FileSnapshotArchiveWriter(base_dir=tmp_path, prefix="studio/rally")
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )
    result = writer.archive(candidate)
    row = _archive_row(result)
    row["format"] = "legacy-json"
    session = FakeSession([], archive_rows=[row])

    with pytest.raises(
        RuntimeError, match="Unsupported contract snapshot archive format"
    ):
        SnapshotArchiveReader(file_dir=tmp_path).load_snapshot(
            session, candidate.tx_hash
        )


def test_archive_reader_rejects_bad_gzip_payload(tmp_path):
    writer = FileSnapshotArchiveWriter(base_dir=tmp_path, prefix="studio/rally")
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )
    result = writer.archive(candidate)
    archive_path = tmp_path / result.key
    bad_payload = b"not gzip"
    archive_path.write_bytes(bad_payload)
    session = FakeSession(
        [],
        archive_rows=[
            {
                "tx_hash": candidate.tx_hash,
                "backend": result.backend,
                "bucket": result.bucket,
                "object_key": result.key,
                "uri": result.uri,
                "format": result.format,
                "snapshot_sha256": result.uncompressed_sha256,
                "compressed_sha256": hashlib.sha256(bad_payload).hexdigest(),
                "snapshot_bytes": result.uncompressed_bytes,
                "compressed_bytes": len(bad_payload),
            }
        ],
    )
    reader = SnapshotArchiveReader(file_dir=tmp_path)

    with pytest.raises(gzip.BadGzipFile):
        reader.load_snapshot(session, candidate.tx_hash)


def test_gcs_archive_writer_uploads_compressed_snapshot_with_metadata():
    client = FakeGCSClient()
    writer = GCSSnapshotArchiveWriter(
        bucket="archive-bucket",
        prefix="studio/rally",
        storage_class="NEARLINE",
        client=client,
    )
    candidate = SnapshotCandidate(
        tx_hash="0xABCDEF",
        status="FINALIZED",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        snapshot_bytes=7,
        snapshot_json='{"x":1}',
    )

    result = writer.archive(candidate)

    blob = client.buckets["archive-bucket"].blobs[result.key]
    assert result.backend == "gcs"
    assert result.bucket == "archive-bucket"
    assert result.uri == f"gs://archive-bucket/{result.key}"
    assert blob.content_encoding == "gzip"
    assert blob.storage_class == "NEARLINE"
    assert blob.metadata["tx-hash"] == "0xABCDEF"
    assert blob.metadata["snapshot-sha256"] == hashlib.sha256(b'{"x":1}').hexdigest()
    assert blob.uploads[0]["content_type"] == "application/json"
    assert gzip.decompress(blob.uploads[0]["data"]) == b'{"x":1}'
    writer.verify(result)
    assert blob.download_calls == [True]


def test_archive_writers_require_bucket_for_verification():
    archive_result = ArchiveResult(
        backend="s3",
        bucket=None,
        key="studio/rally/v1/ab/0xABCDEF.contract_snapshot.json.gz",
        uri="s3://archive-bucket/studio/rally/v1/ab/0xABCDEF.contract_snapshot.json.gz",
        format=ARCHIVE_FORMAT,
        uncompressed_bytes=7,
        compressed_bytes=7,
        uncompressed_sha256="0" * 64,
        compressed_sha256="1" * 64,
        metadata={"tx-hash": "0xABCDEF"},
    )

    with pytest.raises(RuntimeError, match="S3 archive result is missing bucket"):
        S3SnapshotArchiveWriter(
            bucket="archive-bucket",
            prefix="studio/rally",
            client=FakeS3Client(),
        ).verify(archive_result)

    with pytest.raises(RuntimeError, match="GCS archive result is missing bucket"):
        GCSSnapshotArchiveWriter(
            bucket="archive-bucket",
            prefix="studio/rally",
            client=FakeGCSClient(),
        ).verify(archive_result)


def test_archive_reader_rejects_missing_cloud_bucket():
    s3_row = {
        "backend": "s3",
        "bucket": None,
        "object_key": "snapshot.json.gz",
    }
    with pytest.raises(RuntimeError, match="S3 archive row is missing bucket"):
        SnapshotArchiveReader(s3_client=FakeS3Client())._download(s3_row)

    gcs_row = {
        "backend": "gcs",
        "bucket": None,
        "object_key": "snapshot.json.gz",
    }
    with pytest.raises(RuntimeError, match="GCS archive row is missing bucket"):
        SnapshotArchiveReader(gcs_client=FakeGCSClient())._download(gcs_row)


def test_archive_reader_rejects_unknown_backend():
    with pytest.raises(
        RuntimeError, match="Unsupported contract snapshot archive backend"
    ):
        SnapshotArchiveReader()._download({"backend": "ftp"})


def test_pruner_archives_before_pruning_and_commits():
    session = FakeSession([_candidate_row("0x1"), _candidate_row("0x2")])
    writer = RecordingArchiveWriter()
    pruner = TerminalSnapshotPruner(
        lambda: session,
        TerminalSnapshotPrunerConfig(enabled=True, s3_bucket="archive-bucket"),
        archive_writer=writer,
    )

    result = pruner.prune_once()

    assert writer.archived == ["0x1", "0x2"]
    assert writer.verified == ["studio/v1/0x1.json.gz", "studio/v1/0x2.json.gz"]
    assert [row["tx_hash"] for row in session.archive_inserts] == ["0x1", "0x2"]
    assert session.pruned_archive_hashes == ["0x1", "0x2"]
    assert session.updated_hashes == ["0x1", "0x2"]
    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True
    assert result.archived == 2
    assert result.pruned == 2
    assert result.compressed_bytes == 84


def test_pruner_rolls_back_and_does_not_prune_when_archive_fails():
    session = FakeSession([_candidate_row("0x1")])
    pruner = TerminalSnapshotPruner(
        lambda: session,
        TerminalSnapshotPrunerConfig(enabled=True, s3_bucket="archive-bucket"),
        archive_writer=RecordingArchiveWriter(fail=True),
    )

    with pytest.raises(RuntimeError, match="archive failed"):
        pruner.prune_once()

    assert session.updated_hashes == []
    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True


def test_pruner_rolls_back_and_does_not_prune_when_archive_verification_fails():
    session = FakeSession([_candidate_row("0x1")])
    pruner = TerminalSnapshotPruner(
        lambda: session,
        TerminalSnapshotPrunerConfig(enabled=True, s3_bucket="archive-bucket"),
        archive_writer=RecordingArchiveWriter(verify_fail=True),
    )

    with pytest.raises(RuntimeError, match="archive verification failed"):
        pruner.prune_once()

    assert session.archive_inserts == []
    assert session.updated_hashes == []
    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True


def test_pruner_rolls_back_and_does_not_prune_when_archive_index_insert_fails():
    session = FakeSession([_candidate_row("0x1")], fail_on_archive_insert=True)
    writer = RecordingArchiveWriter()
    pruner = TerminalSnapshotPruner(
        lambda: session,
        TerminalSnapshotPrunerConfig(enabled=True, s3_bucket="archive-bucket"),
        archive_writer=writer,
    )

    with pytest.raises(RuntimeError, match="archive index insert failed"):
        pruner.prune_once()

    assert writer.archived == ["0x1"]
    assert writer.verified == ["studio/v1/0x1.json.gz"]
    assert session.updated_hashes == []
    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True


def test_pruner_rolls_back_when_prune_update_fails_after_archive_index_insert():
    session = FakeSession([_candidate_row("0x1")], fail_on_transaction_update=True)
    writer = RecordingArchiveWriter()
    pruner = TerminalSnapshotPruner(
        lambda: session,
        TerminalSnapshotPrunerConfig(enabled=True, s3_bucket="archive-bucket"),
        archive_writer=writer,
    )

    with pytest.raises(RuntimeError, match="transaction prune failed"):
        pruner.prune_once()

    assert writer.archived == ["0x1"]
    assert writer.verified == ["studio/v1/0x1.json.gz"]
    assert [row["tx_hash"] for row in session.archive_inserts] == ["0x1"]
    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True


def test_dry_run_does_not_require_s3_bucket_or_prune():
    session = FakeSession([_candidate_row("0x1")])
    pruner = TerminalSnapshotPruner(
        lambda: session,
        TerminalSnapshotPrunerConfig(enabled=True, dry_run=True, s3_bucket=None),
    )

    result = pruner.prune_once()

    assert result.dry_run is True
    assert result.candidates == 1
    assert result.pruned == 0
    assert session.updated_hashes == []
    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True


def test_pruner_can_prune_without_archive_when_disabled():
    session = FakeSession([_candidate_row("0x1")])
    writer = RecordingArchiveWriter(fail=True)
    pruner = TerminalSnapshotPruner(
        lambda: session,
        TerminalSnapshotPrunerConfig(
            enabled=True,
            archive_enabled=False,
            allow_lossy_prune=True,
            s3_bucket=None,
        ),
        archive_writer=writer,
    )

    result = pruner.prune_once()

    assert writer.archived == []
    assert session.updated_hashes == ["0x1"]
    assert session.committed is True
    assert result.archived == 0
    assert result.pruned == 1


def test_pruner_rolls_back_when_no_candidates_found():
    session = FakeSession([])
    pruner = TerminalSnapshotPruner(
        lambda: session,
        TerminalSnapshotPrunerConfig(enabled=True, s3_bucket="archive-bucket"),
    )

    result = pruner.prune_once()

    assert result == PruneBatchResult(dry_run=False)
    assert session.rolled_back is True
    assert session.closed is True


def test_config_from_environment_reads_pruner_settings(monkeypatch):
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_ENABLED", "true")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_ENABLED", "false")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_VERIFY_ARCHIVE", "false")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_ALLOW_LOSSY_PRUNE", "true")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_DRY_RUN", "yes")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_BATCH_SIZE", "17")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_RETENTION_HOURS", "48")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_INTERVAL_SECONDS", "9")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND", "gcs")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_FILE_DIR", "/tmp/archive")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_BUCKET", "gcs-bucket")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_PREFIX", "gcs-prefix")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_STORAGE_CLASS", "NEARLINE")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_BUCKET", "bucket")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_PREFIX", "prefix")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_STORAGE_CLASS", "STANDARD")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_SSE", "aws:kms")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_KMS_KEY_ID", "alias/key")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    config = TerminalSnapshotPrunerConfig.from_environment()

    assert config.enabled is True
    assert config.archive_enabled is False
    assert config.verify_archive is False
    assert config.allow_lossy_prune is True
    assert config.dry_run is True
    assert config.batch_size == 17
    assert config.retention_hours == 48
    assert config.interval_seconds == 9
    assert config.archive_backend == "gcs"
    assert config.file_dir == "/tmp/archive"
    assert config.gcs_bucket == "gcs-bucket"
    assert config.gcs_prefix == "gcs-prefix"
    assert config.gcs_storage_class == "NEARLINE"
    assert config.s3_bucket == "bucket"
    assert config.s3_prefix == "prefix"
    assert config.s3_region == "eu-west-1"
    assert config.s3_storage_class == "STANDARD"
    assert config.s3_sse == "aws:kms"
    assert config.s3_kms_key_id == "alias/key"


def test_config_from_environment_falls_back_for_bad_numbers(monkeypatch):
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_BATCH_SIZE", "0")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_RETENTION_HOURS", "bad")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_INTERVAL_SECONDS", "")

    config = TerminalSnapshotPrunerConfig.from_environment()

    assert config.batch_size == 5
    assert config.retention_hours == 24
    assert config.interval_seconds == 300


def test_config_from_environment_defaults_to_gcs_when_bucket_is_configured(
    monkeypatch,
):
    monkeypatch.delenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_ARCHIVE_BACKEND", raising=False)
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_GCS_BUCKET", "gcs-bucket")
    monkeypatch.setenv(
        "STUDIO_CONTRACT_SNAPSHOT_ARCHIVE_RETRIEVAL_ENABLED",
        "on",
    )

    config = TerminalSnapshotPrunerConfig.from_environment()

    assert config.archive_backend == "gcs"
    assert snapshot_archive_read_through_enabled() is True


def test_config_validate_requires_bucket_when_archive_enabled():
    config = TerminalSnapshotPrunerConfig(
        enabled=True,
        archive_enabled=True,
        dry_run=False,
        archive_backend="s3",
        s3_bucket=None,
    )

    with pytest.raises(RuntimeError, match="S3_BUCKET is required"):
        config.validate_for_run()


def test_config_validate_rejects_lossy_prune_without_explicit_override():
    config = TerminalSnapshotPrunerConfig(
        enabled=True,
        archive_enabled=False,
        dry_run=False,
    )

    with pytest.raises(RuntimeError, match="Refusing to prune"):
        config.validate_for_run()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (
            TerminalSnapshotPrunerConfig(
                enabled=True,
                archive_backend="archivefs",
                s3_bucket="archive-bucket",
            ),
            "ARCHIVE_BACKEND must be one of",
        ),
        (
            TerminalSnapshotPrunerConfig(
                enabled=True,
                archive_backend="file",
                file_dir="",
            ),
            "FILE_DIR is required",
        ),
        (
            TerminalSnapshotPrunerConfig(
                enabled=True,
                archive_backend="gcs",
                gcs_bucket=None,
            ),
            "GCS_BUCKET is required",
        ),
    ],
)
def test_config_validate_rejects_invalid_archive_settings(config, message):
    with pytest.raises(RuntimeError, match=message):
        config.validate_for_run()


def test_s3_writer_requires_bucket_and_handles_empty_prefix():
    with pytest.raises(RuntimeError, match="S3 bucket is required"):
        S3SnapshotArchiveWriter.from_config(
            TerminalSnapshotPrunerConfig(s3_bucket=None)
        )

    writer = S3SnapshotArchiveWriter(bucket="archive-bucket", prefix="")

    assert writer.key_for_hash("0xA") == "v1/unknown/0xA.contract_snapshot.json.gz"


def test_build_snapshot_archive_writer_selects_backend_classes(tmp_path):
    file_writer = build_snapshot_archive_writer(
        TerminalSnapshotPrunerConfig(archive_backend="file", file_dir=str(tmp_path))
    )
    gcs_writer = build_snapshot_archive_writer(
        TerminalSnapshotPrunerConfig(archive_backend="gcs", gcs_bucket="gcs-bucket")
    )
    s3_writer = build_snapshot_archive_writer(
        TerminalSnapshotPrunerConfig(archive_backend="s3", s3_bucket="s3-bucket")
    )

    assert isinstance(file_writer, FileSnapshotArchiveWriter)
    assert isinstance(gcs_writer, GCSSnapshotArchiveWriter)
    assert isinstance(s3_writer, S3SnapshotArchiveWriter)

    with pytest.raises(
        RuntimeError, match="Unsupported contract snapshot archive backend"
    ):
        build_snapshot_archive_writer(
            TerminalSnapshotPrunerConfig(archive_backend="archivefs")
        )


def test_gcs_writer_requires_bucket():
    with pytest.raises(RuntimeError, match="GCS bucket is required"):
        GCSSnapshotArchiveWriter.from_config(
            TerminalSnapshotPrunerConfig(gcs_bucket=None)
        )


@pytest.mark.asyncio
async def test_pruner_loop_returns_when_disabled():
    await run_terminal_snapshot_pruner_loop(
        lambda: None,
        TerminalSnapshotPrunerConfig(enabled=False),
    )


@pytest.mark.asyncio
async def test_pruner_loop_runs_batch_until_cancelled(monkeypatch):
    created_pruners = []
    slept = []

    class FakePruner:
        def __init__(self, get_session, config, archive_writer):
            self.get_session = get_session
            self.config = config
            self.archive_writer = archive_writer
            created_pruners.append(self)

        def prune_once(self):
            return PruneBatchResult(
                candidates=1,
                archived=1,
                pruned=1,
                logical_bytes=7,
                compressed_bytes=5,
            )

    async def fake_to_thread(callback):
        return callback()

    async def fake_sleep(seconds):
        slept.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(pruner_mod, "TerminalSnapshotPruner", FakePruner)
    monkeypatch.setattr(pruner_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(pruner_mod.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_terminal_snapshot_pruner_loop(
            lambda: None,
            TerminalSnapshotPrunerConfig(
                enabled=True,
                archive_enabled=False,
                allow_lossy_prune=True,
                interval_seconds=11,
            ),
            archive_writer=object(),
        )

    assert len(created_pruners) == 1
    assert slept == [11]


def test_prune_cli_database_url_prefers_explicit_url(monkeypatch):
    monkeypatch.setenv("DB_URL", "postgresql+psycopg2://example/db")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg2://ignored/db")

    assert prune_cli.get_database_url() == "postgresql+psycopg2://example/db"


def test_prune_cli_database_url_uses_state_database_default(monkeypatch):
    monkeypatch.delenv("DB_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("DBNAME", raising=False)
    monkeypatch.setenv("DBUSER", "user")
    monkeypatch.setenv("DBPASSWORD", "secret")
    monkeypatch.setenv("DBHOST", "db")
    monkeypatch.setenv("DBPORT", "15432")

    assert (
        prune_cli.get_database_url()
        == "postgresql+psycopg2://user:secret@db:15432/genlayer_state"
    )


def test_prune_cli_main_runs_until_empty_and_applies_cli_overrides(monkeypatch):
    created_pruners = []
    sleep_calls = []
    engine = object()

    class FakePruner:
        def __init__(self, session_factory, config):
            self.session_factory = session_factory
            self.config = config
            self.results = [
                PruneBatchResult(
                    candidates=2,
                    archived=2,
                    pruned=2,
                    logical_bytes=20,
                    compressed_bytes=10,
                ),
                PruneBatchResult(),
            ]
            created_pruners.append(self)

        def prune_once(self):
            return self.results.pop(0)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prune_terminal_snapshots",
            "--batch-size",
            "7",
            "--retention-hours",
            "12",
            "--sleep-seconds",
            "0.5",
        ],
    )
    monkeypatch.setattr(
        prune_cli.TerminalSnapshotPrunerConfig,
        "from_environment",
        staticmethod(
            lambda: TerminalSnapshotPrunerConfig(
                enabled=False,
                dry_run=False,
                batch_size=5,
                retention_hours=24,
                s3_bucket="archive-bucket",
            )
        ),
    )
    monkeypatch.setattr(prune_cli, "get_database_url", lambda: "postgresql://db")
    monkeypatch.setattr(
        prune_cli,
        "create_engine",
        lambda url, **kwargs: engine,
    )
    monkeypatch.setattr(
        prune_cli,
        "sessionmaker",
        lambda **kwargs: ("SessionLocal", kwargs),
    )
    monkeypatch.setattr(prune_cli, "TerminalSnapshotPruner", FakePruner)
    monkeypatch.setattr(prune_cli.time, "sleep", sleep_calls.append)

    assert prune_cli.main() == 0

    pruner = created_pruners[0]
    session_factory, session_kwargs = pruner.session_factory
    assert session_factory == "SessionLocal"
    assert session_kwargs["bind"] is engine
    assert session_kwargs["expire_on_commit"] is False
    assert pruner.config.enabled is True
    assert pruner.config.batch_size == 7
    assert pruner.config.retention_hours == 12
    assert sleep_calls == [0.5]


def test_prune_cli_main_runs_parallel_workers_with_shared_batch_budget(monkeypatch):
    created_pruners = []
    engine = object()
    call_count = 0
    call_lock = threading.Lock()

    class FakePruner:
        def __init__(self, session_factory, config):
            self.session_factory = session_factory
            self.config = config
            created_pruners.append(self)

        def prune_once(self):
            nonlocal call_count
            with call_lock:
                call_count += 1
            return PruneBatchResult(
                candidates=1,
                archived=1,
                pruned=1,
                logical_bytes=20,
                compressed_bytes=10,
            )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prune_terminal_snapshots",
            "--workers",
            "3",
            "--max-batches",
            "5",
            "--sleep-seconds",
            "0",
        ],
    )
    monkeypatch.setattr(
        prune_cli.TerminalSnapshotPrunerConfig,
        "from_environment",
        staticmethod(
            lambda: TerminalSnapshotPrunerConfig(
                enabled=False,
                dry_run=False,
                batch_size=5,
                retention_hours=24,
                s3_bucket="archive-bucket",
            )
        ),
    )
    monkeypatch.setattr(prune_cli, "get_database_url", lambda: "postgresql://db")
    engine_kwargs = {}
    monkeypatch.setattr(
        prune_cli,
        "create_engine",
        lambda url, **kwargs: engine_kwargs.update(kwargs) or engine,
    )
    monkeypatch.setattr(
        prune_cli,
        "sessionmaker",
        lambda **kwargs: ("SessionLocal", kwargs),
    )
    monkeypatch.setattr(prune_cli, "TerminalSnapshotPruner", FakePruner)

    assert prune_cli.main() == 0

    assert call_count == 5
    assert len(created_pruners) == 3
    assert engine_kwargs["pool_size"] == 3
    assert engine_kwargs["max_overflow"] == 0
