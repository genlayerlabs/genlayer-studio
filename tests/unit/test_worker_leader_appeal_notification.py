from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.consensus.worker import ConsensusWorker


@pytest.mark.asyncio
async def test_leader_appeal_notifies_transaction_subscribers_after_commit():
    tx_hash = "0xappealed"
    worker = MagicMock(spec=ConsensusWorker)
    worker.worker_id = "test-worker"
    worker.validators_manager = MagicMock()
    worker.consensus_algorithm = MagicMock()
    worker.consensus_algorithm.process_leader_appeal = AsyncMock()
    worker.msg_handler = MagicMock()
    worker._generic_error_retries = {}
    worker._leader_crash_retries = {}
    session = MagicMock()

    def check_notification(event, *, log_to_terminal):
        session.commit.assert_called_once_with()
        assert event.name == "transaction_appeal_updated"
        assert event.transaction_hash == tx_hash
        assert event.data == {"hash": tx_hash}
        assert log_to_terminal is False

    worker.msg_handler.send_message.side_effect = check_notification

    await ConsensusWorker.process_appeal(
        worker,
        {
            "hash": tx_hash,
            "from_address": "0xsender",
            "to_address": "0xcontract",
            "type": 2,
            "status": "UNDETERMINED",
        },
        session,
    )

    worker.consensus_algorithm.process_leader_appeal.assert_awaited_once()
    worker.msg_handler.send_message.assert_called_once()
