"""The admin key is compared in constant time, and an odd key is just a wrong key.

``ADMIN_API_KEY`` protects the operations that a hosted Studio keeps away from
anonymous callers. Comparing it with ``==`` returns at the first differing
character, so the time a wrong key takes to be rejected leaks how much of it was
right. All three places that check it use ``hmac.compare_digest`` instead.
"""

import hmac
import os
import random
from unittest.mock import MagicMock, Mock, patch

import pytest

from backend.protocol_rpc import endpoints
from backend.protocol_rpc.endpoints import (
    _admin_key_matches,
    admin_upgrade_contract_code,
    cancel_transaction,
    require_admin_access,
)
from backend.protocol_rpc.exceptions import JSONRPCError

HOSTED_WITH_KEY = {"VITE_IS_HOSTED": "true", "ADMIN_API_KEY": "secret123"}
TX_HASH = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
CONTRACT = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"


@pytest.fixture
def compare_digest_spy():
    with patch.object(
        endpoints.hmac, "compare_digest", wraps=hmac.compare_digest
    ) as spy:
        yield spy


class TestAdminKeyMatches:
    @pytest.mark.parametrize(
        "presented, configured, expected",
        [
            ("secret123", "secret123", True),
            ("secret123", "secret124", False),
            ("secret", "secret123", False),
            ("secret123", "secret", False),
            ("Secret123", "secret123", False),
            ("", "secret123", False),
            ("ключ-🔑", "ключ-🔑", True),
            ("ключ-🔑", "ключ-🗝", False),
            ("\ud800abc", "\ud800abc", True),
            ("\ud800abc", "secret123", False),
        ],
    )
    def test_strings(self, presented, configured, expected):
        assert _admin_key_matches(presented, configured) is expected

    @pytest.mark.parametrize(
        "presented",
        [None, 123, 12.5, True, b"secret123", bytearray(b"secret123"), ["secret123"]],
    )
    def test_values_that_are_not_strings_never_match(self, presented):
        assert _admin_key_matches(presented, "secret123") is False

    def test_agrees_with_plain_equality_for_strings(self):
        rng = random.Random(5)
        chars = ["a", "b", "Z", "0", "é", "日", "🔑", "\ud800", "\x00", " "]
        for _ in range(2000):
            configured = "".join(rng.choice(chars) for _ in range(rng.randint(1, 20)))
            presented = rng.choice(
                [
                    configured,
                    configured[:-1] + rng.choice(chars),
                    configured + rng.choice(chars),
                    "".join(rng.choice(chars) for _ in range(rng.randint(0, 20))),
                ]
            )
            assert _admin_key_matches(presented, configured) == (
                presented == configured
            )


class TestRequireAdminAccess:
    @staticmethod
    def _guarded():
        @require_admin_access
        def guarded(admin_key=None):
            return "ok"

        return guarded

    def test_correct_key_runs_the_function(self):
        with patch.dict(os.environ, HOSTED_WITH_KEY, clear=True):
            assert self._guarded()(admin_key="secret123") == "ok"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"admin_key": "wrong"},
            {"admin_key": "secret12"},
            {"admin_key": ""},
            {},
            {"admin_key": 123},
            {"admin_key": ["secret123"]},
            {"admin_key": "\ud800"},
            {"admin_key": "ключ"},
        ],
    )
    def test_wrong_or_odd_keys_are_rejected_like_any_wrong_key(self, kwargs):
        with patch.dict(os.environ, HOSTED_WITH_KEY, clear=True):
            with pytest.raises(JSONRPCError) as exc_info:
                self._guarded()(**kwargs)
        assert exc_info.value.message == "Invalid or missing admin key"

    def test_compares_in_constant_time(self, compare_digest_spy):
        with patch.dict(os.environ, HOSTED_WITH_KEY, clear=True):
            assert self._guarded()(admin_key="secret123") == "ok"
        assert compare_digest_spy.call_count == 1

    def test_hosted_without_a_configured_key_is_still_blocked(self):
        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}, clear=True):
            with pytest.raises(JSONRPCError) as exc_info:
                self._guarded()(admin_key="anything")
        assert exc_info.value.message == "Operation not available in hosted mode"


class TestInlineAdminChecks:
    """``cancel_transaction`` and ``admin_upgrade_contract_code`` check the key themselves."""

    @staticmethod
    def _cancel_session():
        session = Mock(name="Session")
        tx = Mock()
        tx.from_address = None
        tx.status = Mock()
        tx.status.value = "PENDING"
        session.query.return_value.filter_by.return_value.one_or_none.return_value = tx
        return session

    def test_cancel_compares_in_constant_time(self, compare_digest_spy):
        with patch.dict(os.environ, HOSTED_WITH_KEY, clear=True):
            with pytest.raises(JSONRPCError):
                cancel_transaction(
                    self._cancel_session(), TX_HASH, MagicMock(), admin_key="wrong"
                )
        assert compare_digest_spy.call_count == 1

    @pytest.mark.parametrize("admin_key", [123, ["secret123"], "\ud800", None])
    def test_cancel_rejects_odd_keys_without_crashing(self, admin_key):
        with patch.dict(os.environ, HOSTED_WITH_KEY, clear=True):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self._cancel_session(), TX_HASH, MagicMock(), admin_key=admin_key
                )
        assert exc_info.value.code == -32000

    def test_upgrade_compares_in_constant_time(self, compare_digest_spy):
        with patch.dict(os.environ, HOSTED_WITH_KEY, clear=True):
            with pytest.raises(JSONRPCError):
                admin_upgrade_contract_code(
                    Mock(name="Session"), CONTRACT, "code", admin_key="wrong"
                )
        assert compare_digest_spy.call_count == 1

    @pytest.mark.parametrize("admin_key", [123, ["secret123"], "\ud800", None])
    def test_upgrade_rejects_odd_keys_without_crashing(self, admin_key):
        with patch.dict(os.environ, HOSTED_WITH_KEY, clear=True):
            with pytest.raises(JSONRPCError) as exc_info:
                admin_upgrade_contract_code(
                    Mock(name="Session"), CONTRACT, "code", admin_key=admin_key
                )
        assert exc_info.value.code == -32000
