"""Tests for the explorer query layer (backend.protocol_rpc.explorer.queries).

These tests run against a real PostgreSQL database via the db-sqlalchemy
Docker Compose setup.  They exercise every public query function to ensure
the explorer endpoints won't break.
"""

import base64

import pytest
from sqlalchemy.orm import Session

from backend.database_handler.models import (
    CurrentState,
    LLMProviderDBModel,
    Transactions,
    TransactionStatus,
    Validators,
)
from backend.protocol_rpc.explorer import queries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_counter = 0


def _make_tx(session: Session, **overrides) -> Transactions:
    """Insert a transaction with sensible defaults and return the ORM object."""
    global _counter
    _counter += 1
    defaults = dict(
        hash=f"0x{_counter:064x}",
        status=TransactionStatus.FINALIZED,
        from_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        to_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        input_data=None,
        data={"key": "value"},
        consensus_data=None,
        nonce=_counter,
        value=100,
        type=2,  # contract call
        gaslimit=None,
        leader_only=False,
        r=None,
        s=None,
        v=None,
        appeal_failed=None,
        consensus_history=None,
        timestamp_appeal=None,
        appeal_processing_time=None,
        contract_snapshot=None,
        config_rotation_rounds=3,
        num_of_initial_validators=None,
        last_vote_timestamp=None,
        rotation_count=None,
        leader_timeout_validators=None,
    )
    # triggered_by_hash has init=False on the model — handle separately
    triggered_by_hash = overrides.pop("triggered_by_hash", None)
    defaults.update(overrides)
    tx = Transactions(**defaults)
    session.add(tx)
    session.flush()
    if triggered_by_hash is not None:
        tx.triggered_by_hash = triggered_by_hash
        session.flush()
    return tx


def _make_state(session: Session, address: str, **overrides) -> CurrentState:
    """Insert a CurrentState row."""
    defaults = dict(id=address, data={"storage_key": 42}, balance=1000)
    defaults.update(overrides)
    state = CurrentState(**defaults)
    session.add(state)
    session.flush()
    return state


def _make_validator(session: Session, **overrides) -> Validators:
    global _counter
    _counter += 1
    defaults = dict(
        stake=10,
        config={"temp": 0.7},
        address=f"0xVAL{_counter:036x}",
        provider="openai",
        model="gpt-4o",
        plugin="openai-compatible",
        plugin_config={"api_key_env_var": "KEY"},
        private_key=None,
    )
    defaults.update(overrides)
    v = Validators(**defaults)
    session.add(v)
    session.flush()
    return v


def _make_provider(session: Session, **overrides) -> LLMProviderDBModel:
    global _counter
    _counter += 1
    defaults = dict(
        provider="openai",
        model=f"gpt-4o-{_counter}",
        config={},
        plugin="openai-compatible",
        plugin_config={},
        is_default=False,
    )
    defaults.update(overrides)
    p = LLMProviderDBModel(**defaults)
    session.add(p)
    session.flush()
    return p


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestGetStatsCounts:
    def test_empty_database(self, session: Session):
        result = queries.get_stats_counts(session)
        assert result == {
            "totalTransactions": 0,
            "totalValidators": 0,
            "totalContracts": 0,
        }

    def test_counts_with_data(self, session: Session):
        contract_addr = "0xCONTRACT_001"
        _make_state(session, contract_addr)
        _make_tx(session, to_address=contract_addr, type=1)  # deploy tx
        _make_tx(session)  # unrelated tx
        _make_validator(session)
        session.commit()

        result = queries.get_stats_counts(session)
        assert result["totalTransactions"] == 2
        assert result["totalValidators"] == 1
        assert result["totalContracts"] == 1


class TestGetStats:
    def test_empty_database(self, session: Session):
        result = queries.get_stats(session)
        assert result["totalTransactions"] == 0
        assert result["totalValidators"] == 0
        assert result["totalContracts"] == 0
        assert result["appealedTransactions"] == 0
        assert result["finalizedTransactions"] == 0
        assert result["avgTps24h"] >= 0
        assert result["txVolume14d"] == [] or isinstance(result["txVolume14d"], list)
        assert result["recentTransactions"] == []
        assert "transactionsByStatus" in result
        assert "transactionsByType" in result

    def test_stats_with_transactions(self, session: Session):
        contract_addr = "0xCONTRACT_002"
        _make_state(session, contract_addr)
        _make_tx(session, to_address=contract_addr, type=1, status=TransactionStatus.FINALIZED)
        _make_tx(session, status=TransactionStatus.PENDING)
        _make_tx(session, status=TransactionStatus.FINALIZED, appealed=True)
        _make_validator(session)
        session.commit()

        result = queries.get_stats(session)
        assert result["totalTransactions"] == 3
        assert result["transactionsByStatus"]["FINALIZED"] == 2
        assert result["transactionsByStatus"]["PENDING"] == 1
        assert result["transactionsByType"]["deploy"] == 1
        assert result["transactionsByType"]["call"] == 2
        assert result["appealedTransactions"] == 1
        assert result["finalizedTransactions"] == 2
        assert result["totalContracts"] == 1
        assert len(result["recentTransactions"]) == 3

    def test_recent_transactions_limited_to_10(self, session: Session):
        for _ in range(15):
            _make_tx(session)
        session.commit()

        result = queries.get_stats(session)
        assert len(result["recentTransactions"]) == 10


# ---------------------------------------------------------------------------
# Transactions (paginated)
# ---------------------------------------------------------------------------


class TestGetAllTransactionsPaginated:
    def test_empty(self, session: Session):
        result = queries.get_all_transactions_paginated(session)
        assert result["transactions"] == []
        assert result["pagination"]["total"] == 0
        assert result["pagination"]["totalPages"] == 0

    def test_pagination(self, session: Session):
        for _ in range(5):
            _make_tx(session)
        session.commit()

        page1 = queries.get_all_transactions_paginated(session, page=1, limit=2)
        assert len(page1["transactions"]) == 2
        assert page1["pagination"]["total"] == 5
        assert page1["pagination"]["totalPages"] == 3

        page3 = queries.get_all_transactions_paginated(session, page=3, limit=2)
        assert len(page3["transactions"]) == 1

    def test_filter_single_status(self, session: Session):
        _make_tx(session, status=TransactionStatus.FINALIZED)
        _make_tx(session, status=TransactionStatus.PENDING)
        _make_tx(session, status=TransactionStatus.PENDING)
        session.commit()

        result = queries.get_all_transactions_paginated(session, status="PENDING")
        assert result["pagination"]["total"] == 2
        assert all(tx["status"] == "PENDING" for tx in result["transactions"])

    def test_filter_multi_status_comma_separated(self, session: Session):
        _make_tx(session, status=TransactionStatus.FINALIZED)
        _make_tx(session, status=TransactionStatus.PENDING)
        _make_tx(session, status=TransactionStatus.ACCEPTED)
        session.commit()

        result = queries.get_all_transactions_paginated(session, status="PENDING,ACCEPTED")
        assert result["pagination"]["total"] == 2
        statuses = {tx["status"] for tx in result["transactions"]}
        assert statuses == {"PENDING", "ACCEPTED"}

    def test_filter_invalid_status_returns_empty(self, session: Session):
        _make_tx(session)
        session.commit()

        result = queries.get_all_transactions_paginated(session, status="INVALID")
        assert result["transactions"] == []
        assert result["pagination"]["total"] == 0

    def test_search_by_hash(self, session: Session):
        unique_hash = "0xABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890"
        tx = _make_tx(session, hash=unique_hash)
        _make_tx(session)
        session.commit()

        result = queries.get_all_transactions_paginated(session, search="ABCDEF123456")
        assert result["pagination"]["total"] == 1
        assert result["transactions"][0]["hash"] == unique_hash

    def test_search_by_address(self, session: Session):
        unique_addr = "0x1234567890UNIQUE_SEARCH_ADDR"
        _make_tx(session, from_address=unique_addr)
        _make_tx(session)
        session.commit()

        result = queries.get_all_transactions_paginated(session, search="UNIQUE_SEARCH")
        assert result["pagination"]["total"] == 1

    def test_triggered_counts(self, session: Session):
        parent = _make_tx(session)
        _make_tx(session, triggered_by_hash=parent.hash)
        _make_tx(session, triggered_by_hash=parent.hash)
        session.commit()

        result = queries.get_all_transactions_paginated(session)
        parent_row = next(tx for tx in result["transactions"] if tx["hash"] == parent.hash)
        assert parent_row["triggered_count"] == 2


# ---------------------------------------------------------------------------
# Single transaction with relations
# ---------------------------------------------------------------------------


class TestGetTransactionWithRelations:
    def test_not_found(self, session: Session):
        assert queries.get_transaction_with_relations(session, "0xnonexistent") is None

    def test_simple_transaction(self, session: Session):
        tx = _make_tx(session)
        session.commit()

        result = queries.get_transaction_with_relations(session, tx.hash)
        assert result is not None
        assert result["transaction"]["hash"] == tx.hash
        assert result["triggeredTransactions"] == []
        assert result["parentTransaction"] is None

    def test_with_triggered_and_parent(self, session: Session):
        parent = _make_tx(session)
        child = _make_tx(session, triggered_by_hash=parent.hash)
        grandchild = _make_tx(session, triggered_by_hash=child.hash)
        session.commit()

        # Check child has both parent and triggered
        result = queries.get_transaction_with_relations(session, child.hash)
        assert result["parentTransaction"]["hash"] == parent.hash
        assert len(result["triggeredTransactions"]) == 1
        assert result["triggeredTransactions"][0]["hash"] == grandchild.hash


# ---------------------------------------------------------------------------
# Contracts (state)
# ---------------------------------------------------------------------------


class TestGetAllStates:
    def test_empty(self, session: Session):
        result = queries.get_all_states(session)
        assert result["states"] == []
        assert result["pagination"]["total"] == 0

    def test_only_shows_deployed_contracts(self, session: Session):
        """States without a deploy tx (type=1) should be excluded."""
        addr_with_deploy = "0xDEPLOYED_CONTRACT"
        addr_no_deploy = "0xNO_DEPLOY_STATE"

        _make_state(session, addr_with_deploy)
        _make_tx(session, to_address=addr_with_deploy, type=1)

        _make_state(session, addr_no_deploy)
        _make_tx(session, to_address=addr_no_deploy, type=2)  # call, not deploy
        session.commit()

        result = queries.get_all_states(session)
        ids = [s["id"] for s in result["states"]]
        assert addr_with_deploy in ids
        assert addr_no_deploy not in ids

    def test_search_filter(self, session: Session):
        addr = "0xSEARCHABLE_CONTRACT"
        _make_state(session, addr)
        _make_tx(session, to_address=addr, type=1)

        other = "0xOTHER_CONTRACT"
        _make_state(session, other)
        _make_tx(session, to_address=other, type=1)
        session.commit()

        result = queries.get_all_states(session, search="SEARCHABLE")
        assert result["pagination"]["total"] == 1
        assert result["states"][0]["id"] == addr

    def test_pagination(self, session: Session):
        for i in range(5):
            addr = f"0xPAG_CONTRACT_{i:03d}"
            _make_state(session, addr)
            _make_tx(session, to_address=addr, type=1)
        session.commit()

        result = queries.get_all_states(session, page=1, limit=2)
        assert len(result["states"]) == 2
        assert result["pagination"]["totalPages"] == 3

    def test_includes_tx_count(self, session: Session):
        addr = "0xCOUNT_CONTRACT"
        _make_state(session, addr)
        _make_tx(session, to_address=addr, type=1)
        _make_tx(session, to_address=addr, type=2)
        _make_tx(session, from_address=addr, type=2)
        session.commit()

        result = queries.get_all_states(session)
        state_row = next(s for s in result["states"] if s["id"] == addr)
        assert state_row["tx_count"] == 3


class TestGetStateWithTransactions:
    def test_not_found(self, session: Session):
        assert queries.get_state_with_transactions(session, "0xnonexistent") is None

    def test_returns_state_and_transactions(self, session: Session):
        addr = "0xSTATE_DETAIL"
        _make_state(session, addr, balance=500)
        _make_tx(session, to_address=addr, type=1, from_address="0xCREATOR")
        _make_tx(session, to_address=addr, type=2)
        session.commit()

        result = queries.get_state_with_transactions(session, addr)
        assert result is not None
        assert result["state"]["id"] == addr
        assert result["state"]["balance"] == 500
        assert len(result["transactions"]) == 2
        assert result["creator_info"]["creator_address"] == "0xCREATOR"

    def test_extracts_contract_code(self, session: Session):
        addr = "0xCODE_CONTRACT"
        source = "class MyContract(gl.Contract): pass"
        encoded = base64.b64encode(source.encode()).decode()

        _make_state(session, addr)
        _make_tx(
            session,
            to_address=addr,
            type=1,
            data={"contract_code": encoded},
        )
        session.commit()

        result = queries.get_state_with_transactions(session, addr)
        assert result["contract_code"] == source

    def test_no_contract_code(self, session: Session):
        addr = "0xNO_CODE_CONTRACT"
        _make_state(session, addr)
        _make_tx(session, to_address=addr, type=1, data={"key": "val"})
        session.commit()

        result = queries.get_state_with_transactions(session, addr)
        assert result["contract_code"] is None


# ---------------------------------------------------------------------------
# Address (unified lookup)
# ---------------------------------------------------------------------------


class TestGetAddressInfo:
    def test_not_found(self, session: Session):
        assert queries.get_address_info(session, "0xNOWHERE") is None

    def test_resolves_contract(self, session: Session):
        addr = "0xADDR_CONTRACT"
        _make_state(session, addr)
        _make_tx(session, to_address=addr, type=1, from_address="0xDEPLOYER")
        session.commit()

        result = queries.get_address_info(session, addr)
        assert result["type"] == "CONTRACT"
        assert result["address"] == addr
        assert "state" in result
        assert result["creator_info"]["creator_address"] == "0xDEPLOYER"

    def test_resolves_validator(self, session: Session):
        v = _make_validator(session)
        session.commit()

        result = queries.get_address_info(session, v.address)
        assert result["type"] == "VALIDATOR"
        assert result["validator"]["provider"] == v.provider

    def test_resolves_account_with_transactions(self, session: Session):
        account = "0xACCOUNT_WITH_TXS"
        _make_tx(session, from_address=account)
        _make_tx(session, from_address=account)
        session.commit()

        result = queries.get_address_info(session, account)
        assert result["type"] == "ACCOUNT"
        assert result["tx_count"] == 2
        assert len(result["transactions"]) == 2

    def test_resolves_eoa_with_state_but_no_deploy(self, session: Session):
        addr = "0xEOA_WITH_STATE"
        _make_state(session, addr, balance=999)
        session.commit()

        result = queries.get_address_info(session, addr)
        assert result["type"] == "ACCOUNT"
        assert result["balance"] == 999
        assert result["transactions"] == []


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


class TestGetAllValidators:
    def test_empty(self, session: Session):
        result = queries.get_all_validators(session)
        assert result == {"validators": []}

    def test_returns_all(self, session: Session):
        _make_validator(session, provider="openai", model="gpt-4o")
        _make_validator(session, provider="anthropic", model="claude")
        session.commit()

        result = queries.get_all_validators(session)
        assert len(result["validators"]) == 2

    def test_search(self, session: Session):
        _make_validator(session, provider="openai", model="gpt-4o")
        _make_validator(session, provider="anthropic", model="claude-3")
        session.commit()

        result = queries.get_all_validators(session, search="anthropic")
        assert len(result["validators"]) == 1
        assert result["validators"][0]["provider"] == "anthropic"

    def test_limit(self, session: Session):
        for _ in range(5):
            _make_validator(session)
        session.commit()

        result = queries.get_all_validators(session, limit=3)
        assert len(result["validators"]) == 3


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class TestGetAllProviders:
    def test_empty(self, session: Session):
        result = queries.get_all_providers(session)
        assert result == {"providers": []}

    def test_returns_all_ordered(self, session: Session):
        _make_provider(session, provider="openai", model="gpt-4o")
        _make_provider(session, provider="anthropic", model="claude-3")
        session.commit()

        result = queries.get_all_providers(session)
        assert len(result["providers"]) == 2
        # Ordered by provider, model
        assert result["providers"][0]["provider"] == "anthropic"
        assert result["providers"][1]["provider"] == "openai"
