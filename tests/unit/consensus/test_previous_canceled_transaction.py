from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.database_handler.models import TransactionStatus
from tests.unit.consensus.test_helpers import (
    consensus_algorithm,  # noqa: F401 - imported so pytest can discover fixture
    init_dummy_transaction,
)


@pytest.mark.asyncio
async def test_exec_transaction_continues_after_previous_canceled(consensus_algorithm):
    transaction = init_dummy_transaction()
    transactions_processor = MagicMock()
    transactions_processor.get_previous_transaction.return_value = {
        "appealed": False,
        "appeal_undetermined": False,
        "appeal_leader_timeout": False,
        "appeal_validators_timeout": False,
        "status": TransactionStatus.CANCELED.value,
    }

    with patch(
        "backend.consensus.base.PendingState.handle",
        new_callable=AsyncMock,
        return_value=None,
    ) as handle_mock:
        await consensus_algorithm.exec_transaction(
            transaction=transaction,
            transactions_processor=transactions_processor,
            chain_snapshot=MagicMock(),
            accounts_manager=MagicMock(),
            contract_snapshot_factory=lambda _address: MagicMock(),
            contract_processor=MagicMock(),
            node_factory=MagicMock(),
            validators_snapshot=MagicMock(),
        )

    transactions_processor.get_previous_transaction.assert_called_once_with(
        transaction.hash
    )
    handle_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_exec_transaction_waits_when_previous_in_processing(consensus_algorithm):
    transaction = init_dummy_transaction()
    transactions_processor = MagicMock()
    transactions_processor.get_previous_transaction.return_value = {
        "appealed": False,
        "appeal_undetermined": False,
        "appeal_leader_timeout": False,
        "appeal_validators_timeout": False,
        "status": TransactionStatus.PROPOSING.value,
    }

    with patch(
        "backend.consensus.base.PendingState.handle",
        new_callable=AsyncMock,
        return_value=None,
    ) as handle_mock:
        await consensus_algorithm.exec_transaction(
            transaction=transaction,
            transactions_processor=transactions_processor,
            chain_snapshot=MagicMock(),
            accounts_manager=MagicMock(),
            contract_snapshot_factory=lambda _address: MagicMock(),
            contract_processor=MagicMock(),
            node_factory=MagicMock(),
            validators_snapshot=MagicMock(),
        )

    handle_mock.assert_not_awaited()
