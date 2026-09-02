from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.rollup.consensus_service import ConsensusService


def _service_with_contract(contract):
    service = ConsensusService.__new__(ConsensusService)
    service.web3 = MagicMock()
    service.web3.is_connected.return_value = True
    service.web3.eth.get_transaction_count.return_value = 7
    service.web3.eth.account.sign_transaction.return_value = SimpleNamespace(
        raw_transaction=b"signed"
    )
    service._get_contract = MagicMock(return_value=contract)
    service.forward_transaction = MagicMock(return_value={"status": 1})
    return service


def test_fee_aware_submission_uses_funded_shadow_account_and_requires_event():
    contract = MagicMock()
    contract.address = "0x2222222222222222222222222222222222222222"
    service = ConsensusService.__new__(ConsensusService)
    service.web3 = MagicMock()
    service.web3.is_connected.return_value = True
    service.web3.eth.accounts = ["0x1111111111111111111111111111111111111111"]
    service.web3.eth.send_transaction.return_value = b"shadow-hash"
    service.web3.eth.wait_for_transaction_receipt.return_value = {"status": 1}
    service._get_contract = MagicMock(return_value=contract)
    service.wait_new_transaction_event = MagicMock(
        return_value={"tx_id": b"child", "recipient": contract.address}
    )
    service.forward_transaction = MagicMock()

    calldata = "0x35a251fb" + f"{32:064x}" + ("00" * 32)
    authoritative_sender = "0x3333333333333333333333333333333333333333"
    result = service.add_transaction(
        "0xraw",
        authoritative_sender,
        calldata=calldata,
    )

    assert result["recipient"] == contract.address
    service.web3.eth.send_transaction.assert_called_once_with(
        {
            "from": "0x1111111111111111111111111111111111111111",
            "to": contract.address,
            "data": (
                "0x35a251fb" + f"{32:064x}" + ("00" * 12) + authoritative_sender[2:]
            ),
            "value": 0,
        }
    )
    service.forward_transaction.assert_not_called()


def test_retried_message_phase_recovers_stored_child_ids_without_new_events():
    contract = MagicMock()
    contract.functions.emitTransactionAccepted.return_value.build_transaction.return_value = (
        {}
    )
    contract.events.NewTransaction.return_value.process_receipt.return_value = []
    child_ids = [bytes.fromhex("11" * 32), bytes.fromhex("22" * 32)]
    contract.functions.getInternalMessageTxIds.return_value.call.return_value = (
        child_ids
    )
    child_recipients = [
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
    ]
    contract.functions.getInternalMessageRecipients.return_value.call.return_value = (
        child_recipients
    )
    service = _service_with_contract(contract)
    parent_id = "0x" + "aa" * 32

    result = service.emit_transaction_event(
        "emitTransactionAccepted",
        {"address": "0x" + "01" * 20, "private_key": "0x" + "02" * 32},
        parent_id,
        [],
    )

    assert result["tx_ids_hex"] == [
        "0x" + "11" * 32,
        "0x" + "22" * 32,
    ]
    assert result["recipients"] == child_recipients
    contract.functions.getInternalMessageTxIds.assert_called_once_with(
        parent_id, True, []
    )
    contract.functions.getInternalMessageRecipients.assert_called_once_with(
        parent_id, True, []
    )


def test_legacy_bridge_falls_back_to_child_ids_from_receipt_events():
    contract = MagicMock()
    contract.functions.emitTransactionFinalized.return_value.build_transaction.return_value = (
        {}
    )
    contract.events.NewTransaction.return_value.process_receipt.return_value = [
        {
            "args": {
                "txId": bytes.fromhex("33" * 32),
                "recipient": "0x3333333333333333333333333333333333333333",
            }
        }
    ]
    contract.functions.getInternalMessageTxIds.return_value.call.side_effect = (
        RuntimeError("legacy deployment")
    )
    contract.functions.getInternalMessageRecipients.return_value.call.side_effect = (
        RuntimeError("legacy deployment")
    )
    service = _service_with_contract(contract)
    parent_id = "0x" + "bb" * 32

    result = service.emit_transaction_event(
        "emitTransactionFinalized",
        {"address": "0x" + "01" * 20, "private_key": "0x" + "02" * 32},
        parent_id,
        [],
    )

    assert result["tx_ids_hex"] == ["0x" + "33" * 32]
    assert result["recipients"] == ["0x3333333333333333333333333333333333333333"]
    contract.functions.getInternalMessageTxIds.assert_called_once_with(
        parent_id, False, []
    )
    contract.functions.getInternalMessageRecipients.assert_called_once_with(
        parent_id, False, []
    )


def test_forwarding_is_skipped_when_no_rollup_is_attached():
    """The load-test compose runs jsonrpc + consensus-worker with no hardhat.

    emit_transaction_event returns None there by design, and consensus must
    be able to tell that apart from a forwarding failure.
    """
    service = ConsensusService.__new__(ConsensusService)
    service.web3 = MagicMock()
    service.web3.is_connected.return_value = False

    account = {"address": "0x" + "01" * 20, "private_key": "0x" + "02" * 32}

    assert service.transaction_forwarding_skipped(account) is True
    assert service.emit_transaction_event("emitTransactionAccepted", account) is None


def test_forwarding_is_skipped_when_the_account_has_no_private_key():
    service = ConsensusService.__new__(ConsensusService)
    service.web3 = MagicMock()
    service.web3.is_connected.return_value = True

    assert service.transaction_forwarding_skipped({"address": "0x" + "01" * 20}) is True


def test_forwarding_is_active_for_a_connected_node_with_a_key():
    service = ConsensusService.__new__(ConsensusService)
    service.web3 = MagicMock()
    service.web3.is_connected.return_value = True

    assert (
        service.transaction_forwarding_skipped(
            {"address": "0x" + "01" * 20, "private_key": "0x" + "02" * 32}
        )
        is False
    )
