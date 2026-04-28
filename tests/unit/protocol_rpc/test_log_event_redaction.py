from backend.protocol_rpc.message_handler.types import (
    EventScope,
    EventType,
    LogEvent,
    sanitize_log_data,
)


def test_log_event_to_dict_removes_private_key_fields_recursively():
    original = {
        "node_config": {
            "address": "0xvalidator",
            "private_key": "0xabc123",
            "primary_model": {"provider": "openai"},
        },
        "validators": [
            {
                "address": "0xvalidator2",
                "privateKey": "0xdef456",
            }
        ],
        "account_private_key": "0x789",
    }

    event = LogEvent(
        name="consensus_event",
        type=EventType.INFO,
        scope=EventScope.CONSENSUS,
        message="Reached consensus",
        data=original,
    )

    payload = event.to_dict()

    assert payload["data"] == {
        "node_config": {
            "address": "0xvalidator",
            "primary_model": {"provider": "openai"},
        },
        "validators": [{"address": "0xvalidator2"}],
    }
    # The execution object handed to LogEvent is not mutated.
    assert original["node_config"]["private_key"] == "0xabc123"
    assert original["validators"][0]["privateKey"] == "0xdef456"


def test_log_event_to_dict_can_show_private_keys_for_local_debug(monkeypatch):
    monkeypatch.setenv("SHOW_VALIDATOR_PRIVATE_KEYS_IN_LOGS", "true")
    original = {
        "node_config": {
            "address": "0xvalidator",
            "private_key": "0xabc123",
        }
    }

    event = LogEvent(
        name="consensus_event",
        type=EventType.INFO,
        scope=EventScope.CONSENSUS,
        message="Reached consensus",
        data=original,
    )

    assert event.to_dict()["data"] == original


def test_sanitize_log_data_handles_nested_lists_and_tuples():
    result = sanitize_log_data(
        [
            {"private-key": "0xabc", "address": "0x1"},
            ({"private_key": "0xdef", "address": "0x2"},),
        ]
    )

    assert result == [{"address": "0x1"}, ({"address": "0x2"},)]


def test_sanitize_log_data_removes_private_key_attributes_from_objects():
    class ValidatorLike:
        def __init__(self):
            self.address = "0xvalidator"
            self.private_key = "0xabc"

    assert sanitize_log_data({"validator": ValidatorLike()}) == {
        "validator": {"address": "0xvalidator"}
    }
