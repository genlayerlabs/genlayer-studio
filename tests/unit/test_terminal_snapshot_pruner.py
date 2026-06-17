import base64
import gzip
import hashlib
import sys
from datetime import datetime, timezone

import pytest

from backend.database_handler import prune_terminal_snapshots as prune_cli
from backend.database_handler.terminal_snapshot_pruner import (
    PruneBatchResult,
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


def test_pruner_can_prune_without_archive_when_disabled():
    session = FakeSession([_candidate_row("0x1")])
    writer = RecordingArchiveWriter(fail=True)
    pruner = TerminalSnapshotPruner(
        lambda: session,
        TerminalSnapshotPrunerConfig(
            enabled=True, archive_enabled=False, s3_bucket=None
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
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_DRY_RUN", "yes")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_BATCH_SIZE", "17")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_RETENTION_HOURS", "48")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_INTERVAL_SECONDS", "9")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_BUCKET", "bucket")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_PREFIX", "prefix")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_STORAGE_CLASS", "STANDARD")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_SSE", "aws:kms")
    monkeypatch.setenv("STUDIO_CONTRACT_SNAPSHOT_PRUNER_S3_KMS_KEY_ID", "alias/key")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    config = TerminalSnapshotPrunerConfig.from_environment()

    assert config.enabled is True
    assert config.archive_enabled is False
    assert config.dry_run is True
    assert config.batch_size == 17
    assert config.retention_hours == 48
    assert config.interval_seconds == 9
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


def test_config_validate_requires_bucket_when_archive_enabled():
    config = TerminalSnapshotPrunerConfig(
        enabled=True, archive_enabled=True, dry_run=False, s3_bucket=None
    )

    with pytest.raises(RuntimeError, match="S3_BUCKET is required"):
        config.validate_for_run()


def test_s3_writer_requires_bucket_and_handles_empty_prefix():
    with pytest.raises(RuntimeError, match="S3 bucket is required"):
        S3SnapshotArchiveWriter.from_config(
            TerminalSnapshotPrunerConfig(s3_bucket=None)
        )

    writer = S3SnapshotArchiveWriter(bucket="archive-bucket", prefix="")

    assert writer.key_for_hash("0xA") == "v1/unknown/0xA.contract_snapshot.json.gz"


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
    assert pruner.config.enabled is True
    assert pruner.config.batch_size == 7
    assert pruner.config.retention_hours == 12
    assert sleep_calls == [0.5]
