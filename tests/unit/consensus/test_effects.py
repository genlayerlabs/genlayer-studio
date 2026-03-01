"""Tests for effect dataclass construction and properties."""

import pytest
from backend.consensus.effects import (
    Effect,
    AddTimestampEffect,
    StatusUpdateEffect,
    SendMessageEffect,
    EmitRollupEventEffect,
    DBWriteEffect,
    RegisterContractEffect,
    UpdateContractStateEffect,
    InsertTriggeredTransactionEffect,
    UpdateConsensusHistoryEffect,
    SetTransactionResultEffect,
    SetAppealEffect,
    SetAppealUndeterminedEffect,
    SetAppealLeaderTimeoutEffect,
    SetAppealValidatorsTimeoutEffect,
    SetAppealFailedEffect,
    SetAppealProcessingTimeEffect,
    ResetAppealProcessingTimeEffect,
    SetTimestampAppealEffect,
    SetTimestampAwaitingFinalizationEffect,
    SetContractSnapshotEffect,
    SetLeaderTimeoutValidatorsEffect,
    ResetRotationCountEffect,
    IncreaseRotationCountEffect,
    SetTimestampLastVoteEffect,
)


class TestEffectsAreFrozen:
    def test_add_timestamp_is_frozen(self):
        e = AddTimestampEffect(tx_hash="0xabc", state_name="PENDING")
        with pytest.raises(AttributeError):
            e.tx_hash = "changed"

    def test_status_update_is_frozen(self):
        e = StatusUpdateEffect(tx_hash="0xabc", new_status="ACCEPTED")
        with pytest.raises(AttributeError):
            e.new_status = "FINALIZED"

    def test_send_message_is_frozen(self):
        e = SendMessageEffect(
            event_name="test", event_type="info", event_scope="Consensus", message="hi"
        )
        with pytest.raises(AttributeError):
            e.message = "changed"


class TestEffectConstruction:
    def test_add_timestamp(self):
        e = AddTimestampEffect(tx_hash="0x1", state_name="PROPOSING")
        assert e.tx_hash == "0x1"
        assert e.state_name == "PROPOSING"
        assert isinstance(e, Effect)

    def test_status_update_defaults(self):
        e = StatusUpdateEffect(tx_hash="0x1", new_status="ACCEPTED")
        assert e.update_current_status_changes is True

    def test_status_update_custom(self):
        e = StatusUpdateEffect(
            tx_hash="0x1", new_status="ACCEPTED", update_current_status_changes=False
        )
        assert e.update_current_status_changes is False

    def test_send_message_defaults(self):
        e = SendMessageEffect(
            event_name="ev", event_type="info", event_scope="Consensus", message="msg"
        )
        assert e.data is None
        assert e.tx_hash is None
        assert e.log_to_terminal is True

    def test_send_message_full(self):
        e = SendMessageEffect(
            event_name="ev",
            event_type="error",
            event_scope="Consensus",
            message="failed",
            data={"key": "val"},
            tx_hash="0xabc",
            log_to_terminal=False,
        )
        assert e.data == {"key": "val"}
        assert e.log_to_terminal is False

    def test_emit_rollup_event(self):
        account = {"address": "0xleader"}
        e = EmitRollupEventEffect(
            event_name="emitTransactionActivated",
            account=account,
            tx_hash="0x1",
            extra_args=("0xleader", ["0xleader", "0xval1"]),
        )
        assert e.extra_args == ("0xleader", ["0xleader", "0xval1"])

    def test_emit_rollup_event_defaults(self):
        e = EmitRollupEventEffect(
            event_name="emitTransactionLeaderTimeout",
            account={"address": "0x1"},
            tx_hash="0x2",
        )
        assert e.extra_args == ()

    def test_db_write_effect(self):
        e = DBWriteEffect(
            method_name="set_transaction_result", args=("0x1", {"votes": {}})
        )
        assert e.method_name == "set_transaction_result"
        assert e.kwargs == {}

    def test_register_contract(self):
        e = RegisterContractEffect(contract_data={"id": "0xcontract", "data": {}})
        assert e.contract_data["id"] == "0xcontract"

    def test_update_contract_state(self):
        e = UpdateContractStateEffect(
            address="0xcontract", accepted_state={"key": "val"}
        )
        assert e.finalized_state is None

    def test_insert_triggered_transaction(self):
        e = InsertTriggeredTransactionEffect(
            from_address="0xsender",
            to_address="0xrecipient",
            data={"calldata": "0x"},
            value=0,
            tx_type="RUN_CONTRACT",
            nonce=5,
            leader_only=False,
            num_of_initial_validators=5,
            triggered_by_hash="0xparent",
            transaction_hash="0xchild",
            config_rotation_rounds=3,
            sim_config=None,
            triggered_on="accepted",
            execution_mode="NORMAL",
        )
        assert e.from_address == "0xsender"
        assert e.triggered_on == "accepted"

    def test_set_appeal_effect(self):
        e = SetAppealEffect(tx_hash="0x1", appealed=True)
        assert e.appealed is True

    def test_set_appeal_failed(self):
        e = SetAppealFailedEffect(tx_hash="0x1", count=2)
        assert e.count == 2

    def test_set_timestamp_appeal(self):
        e = SetTimestampAppealEffect(tx_hash="0x1", value=None)
        assert e.value is None

    def test_set_contract_snapshot(self):
        e = SetContractSnapshotEffect(tx_hash="0x1", snapshot_dict={"state": {}})
        assert e.snapshot_dict == {"state": {}}

    def test_set_leader_timeout_validators(self):
        e = SetLeaderTimeoutValidatorsEffect(
            tx_hash="0x1", validators=[{"address": "0xval"}]
        )
        assert len(e.validators) == 1


class TestEffectEquality:
    def test_same_effects_are_equal(self):
        e1 = AddTimestampEffect(tx_hash="0x1", state_name="PENDING")
        e2 = AddTimestampEffect(tx_hash="0x1", state_name="PENDING")
        assert e1 == e2

    def test_different_effects_are_not_equal(self):
        e1 = AddTimestampEffect(tx_hash="0x1", state_name="PENDING")
        e2 = AddTimestampEffect(tx_hash="0x1", state_name="PROPOSING")
        assert e1 != e2

    def test_different_types_are_not_equal(self):
        e1 = AddTimestampEffect(tx_hash="0x1", state_name="PENDING")
        e2 = ResetRotationCountEffect(tx_hash="0x1")
        assert e1 != e2
