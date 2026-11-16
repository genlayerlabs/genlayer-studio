from unittest.mock import Mock

import pytest

from tests.integration.icontracts.tests import test_descendant_grants as integration


def test_wait_for_descendants_visits_all_finalized_levels(monkeypatch):
    receipts = {
        "child": {"triggered_transactions": ["grandchild", "sibling"]},
        "grandchild": {"triggered_transactions": ["great-grandchild"]},
        "sibling": {"triggered_transactions": []},
        "great-grandchild": {"triggered_transactions": []},
    }
    client = Mock()
    config = Mock()
    config.get_default_wait_interval.return_value = 3000
    config.get_default_wait_retries.return_value = 50
    wait = Mock(
        side_effect=lambda _client, **kwargs: receipts[kwargs["transaction_hash"]]
    )
    monkeypatch.setattr(integration, "get_gl_client", lambda: client)
    monkeypatch.setattr(integration, "get_general_config", lambda: config)
    monkeypatch.setattr(integration, "wait_for_transaction_receipt", wait)

    integration._wait_for_descendants({"triggered_transactions": ["child"]})

    assert {call.kwargs["transaction_hash"] for call in wait.call_args_list} == set(
        receipts
    )
    assert wait.call_count == len(receipts)
    for call in wait.call_args_list:
        assert call.args == (client,)
        assert call.kwargs["wait_until"] == "finalized"
        assert call.kwargs["interval"] == 3000
        assert call.kwargs["retries"] == 50


def test_wait_for_descendants_propagates_timeout(monkeypatch):
    monkeypatch.setattr(integration, "get_gl_client", Mock())
    monkeypatch.setattr(integration, "get_general_config", Mock())
    monkeypatch.setattr(
        integration, "wait_for_transaction_receipt", Mock(side_effect=TimeoutError)
    )

    with pytest.raises(TimeoutError):
        integration._wait_for_descendants({"triggered_transactions": ["child"]})
