import base64
import gzip
import hashlib
from datetime import datetime, timezone

import pytest

from backend.database_handler.terminal_snapshot_pruner import (
    S3SnapshotArchiveWriter,
    SnapshotCandidate,
    TerminalSnapshotPruner,
    TerminalSnapshotPrunerConfig,
)


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


class FakeMappingsResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeUpdateResult:
    rowcount = 1


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.updated_hashes = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT" in sql:
            return FakeMappingsResult(self.rows)
        if "UPDATE transactions" in sql:
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
    def __init__(self, fail=False):
        self.fail = fail
        self.archived = []

    def archive(self, candidate):
        if self.fail:
            raise RuntimeError("archive failed")
        self.archived.append(candidate.tx_hash)
        return type(
            "ArchiveResult",
            (),
            {
                "compressed_bytes": 42,
            },
        )()


def _candidate_row(tx_hash="0xabc"):
    snapshot_json = '{"state":{"accepted":{"a":"b"},"finalized":{"a":"b"}}}'
    return {
        "hash": tx_hash,
        "status": "FINALIZED",
        "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "snapshot_bytes": len(snapshot_json),
        "snapshot_json": snapshot_json,
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
