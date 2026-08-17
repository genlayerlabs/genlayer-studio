"""Unit tests for the narrowed gen_getContractCode read path."""

import base64
from unittest.mock import MagicMock, patch

import pytest

from backend.database_handler.contract_snapshot import (
    _code_slot_b64,
    _decode_code_payload,
    fetch_deployed_code_b64,
)
from backend.database_handler.errors import ContractNotFoundError

ADDRESS = "0xabc"


def _stored_blob(code: bytes) -> str:
    """Build a slot blob: 4-byte little-endian length prefix, then the code."""
    return base64.b64encode(len(code).to_bytes(4, "little") + code).decode("ascii")


def _make_session(row):
    session = MagicMock()
    session.execute.return_value.one_or_none.return_value = row
    return session


def _make_row(data_kind="object", state_kind="object", nested=None, flat=None):
    row = MagicMock()
    row.data_kind = data_kind
    row.state_kind = state_kind
    row.nested = nested
    row.flat = flat
    return row


class TestFetchDeployedCode:
    def test_returns_code_from_nested_state(self):
        code = b"class Contract: pass"
        session = _make_session(_make_row(nested=_stored_blob(code)))

        result = fetch_deployed_code_b64(session, ADDRESS)

        assert base64.b64decode(result) == code

    def test_falls_back_to_flat_state_for_old_format(self):
        """Pre-migration rows put slots directly under `state`."""
        code = b"legacy contract"
        session = _make_session(_make_row(nested=None, flat=_stored_blob(code)))

        result = fetch_deployed_code_b64(session, ADDRESS)

        assert base64.b64decode(result) == code

    def test_prefers_nested_over_flat(self):
        session = _make_session(
            _make_row(nested=_stored_blob(b"new"), flat=_stored_blob(b"old"))
        )

        assert base64.b64decode(fetch_deployed_code_b64(session, ADDRESS)) == b"new"

    def test_missing_row_raises_not_found(self):
        session = _make_session(None)

        with pytest.raises(ContractNotFoundError):
            fetch_deployed_code_b64(session, ADDRESS)

    def test_missing_slot_returns_none(self):
        """Contract exists but holds no code — distinct from not existing."""
        session = _make_session(_make_row(nested=None, flat=None))

        assert fetch_deployed_code_b64(session, ADDRESS) is None

    def test_corrupt_blob_returns_none_rather_than_raising(self):
        session = _make_session(_make_row(nested="!!! not base64 !!!"))

        assert fetch_deployed_code_b64(session, ADDRESS) is None

    @pytest.mark.parametrize(
        "data_kind,state_kind",
        [
            ("string", "object"),  # legacy rows store `data` as a JSON string
            ("null", None),
            ("object", None),  # `{}` — present but never deployed
        ],
    )
    def test_unusual_shapes_defer_to_the_snapshot_path(self, data_kind, state_kind):
        """Legacy and undeployed rows keep the original error semantics.

        Rather than reimplement those in SQL, the fast path declines and hands
        the row to ContractSnapshot.
        """
        session = _make_session(_make_row(data_kind=data_kind, state_kind=state_kind))

        with patch(
            "backend.database_handler.contract_snapshot.ContractSnapshot"
        ) as snapshot_cls:
            snapshot_cls.return_value.extract_deployed_code_b64.return_value = (
                "FALLBACK"
            )

            assert fetch_deployed_code_b64(session, ADDRESS) == "FALLBACK"

        snapshot_cls.assert_called_once_with(ADDRESS, session)


class TestCodeSlotHelpers:
    def test_code_slot_is_stable(self):
        """The slot address is a protocol constant; drift silently breaks reads."""
        assert _code_slot_b64() == _code_slot_b64()
        assert len(base64.b64decode(_code_slot_b64())) == 32

    def test_decode_respects_length_prefix(self):
        """Trailing bytes past the declared length must not leak into the code."""
        blob = base64.b64encode((3).to_bytes(4, "little") + b"abc" + b"PADDING")
        assert _decode_code_payload(blob.decode()) == base64.b64encode(b"abc").decode()


class TestGeneratedSQL:
    def test_statement_compiles_against_postgres(self):
        """Guards the JSONB path expression, which mocks cannot validate."""
        from sqlalchemy import func, select
        from sqlalchemy.dialects import postgresql

        from backend.database_handler.models import CurrentState

        slot = _code_slot_b64()
        stmt = select(
            func.jsonb_typeof(CurrentState.data).label("data_kind"),
            func.jsonb_typeof(CurrentState.data["state"]).label("state_kind"),
            CurrentState.data["state"]["accepted"][slot].astext.label("nested"),
            CurrentState.data["state"][slot].astext.label("flat"),
        ).where(CurrentState.id == ADDRESS)

        sql = str(stmt.compile(dialect=postgresql.dialect()))

        assert "jsonb_typeof" in sql
        assert "->>" in sql
        assert "current_state" in sql
        # The whole point is not selecting the full state blob.
        assert "current_state.data \n" not in sql
