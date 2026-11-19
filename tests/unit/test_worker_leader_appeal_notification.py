from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.consensus.worker import ConsensusWorker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "processor_name", "publish_fails"),
    [
        ("UNDETERMINED", "process_leader_appeal", False),
        ("LEADER_TIMEOUT", "process_leader_timeout_appeal", False),
        ("ACCEPTED", "process_validator_appeal", False),
        ("VALIDATORS_TIMEOUT", "process_validator_appeal", False),
        ("UNDETERMINED", "process_leader_appeal", True),
    ],
)
async def test_appeal_notifies_transaction_subscribers_after_commit(
    status, processor_name, publish_fails
):
    tx_hash = "0xappealed"
    validators_manager = MagicMock()
    validators_manager.snapshot.return_value = MagicMock()
    worker = ConsensusWorker(
        get_session=MagicMock(),
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=validators_manager,
        genvm_manager=MagicMock(),
        worker_id="test-worker",
    )
    session = MagicMock()
    commits_at_publish = []

    def publish(*args, **kwargs):
        commits_at_publish.append(session.commit.call_count)
        if publish_fails:
            raise RuntimeError("notification unavailable")

    worker.msg_handler.send_message.side_effect = publish

    with (
        patch.object(
            worker.consensus_algorithm, processor_name, new_callable=AsyncMock
        ) as process,
        patch.object(worker, "release_transaction") as release,
        patch.object(worker, "_restore_admitted_appeal_for_retry") as restore,
        patch.object(worker, "_handle_generic_error_retry") as retry,
    ):
        await worker.process_appeal(
            {
                "hash": tx_hash,
                "from_address": "0xsender",
                "to_address": "0xcontract",
                "type": 2,
                "status": status,
            },
            session,
        )

    process.assert_awaited_once()
    release.assert_called_once()
    restore.assert_not_called()
    retry.assert_not_called()
    session.rollback.assert_not_called()
    session.commit.assert_called_once_with()
    assert commits_at_publish == [1]
    worker.msg_handler.send_message.assert_called_once()
    args, kwargs = worker.msg_handler.send_message.call_args
    (event,) = args
    assert event.name == "transaction_appeal_updated"
    assert event.transaction_hash == tx_hash
    assert event.data == {"hash": tx_hash}
    assert event.message == "Appeal finished"
    assert kwargs == {"log_to_terminal": False}
