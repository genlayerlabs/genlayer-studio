"""
Regression test: sim_getConsensusContract should produce a soft NotFoundError
(structured JSON-RPC error) when the requested consensus contract has no
deployment data on this instance — not a bare Exception.

Hosted Studio doesn't ship the hardhat deployment artifacts for the
rollup-side consensus contracts (Queues / RevealingPhase / IdlenessPhase).
Newer genlayer-js carries that info in chain config and never calls this
RPC. Older clients still fall through to it. Pre-fix, every fallback call
spammed the jsonrpc log with:

    Unexpected error in sim_getConsensusContract:
    Failed to load Queues deployment data

…via the bare-Exception print() in rpc_endpoint_manager. Post-fix,
consensus_service.load_contract returns None, endpoints.get_contract
raises NotFoundError, and the RPC framework routes it through the quiet
soft-error path.
"""

from unittest.mock import MagicMock

import pytest

from backend.protocol_rpc import endpoints
from backend.protocol_rpc.exceptions import NotFoundError


def test_get_contract_raises_NotFoundError_when_load_contract_returns_None():
    consensus_service = MagicMock()
    consensus_service.load_contract.return_value = None

    with pytest.raises(NotFoundError) as exc_info:
        endpoints.get_contract(consensus_service, "Queues")

    assert "Queues" in exc_info.value.message
    # NotFoundError code is the standard "soft not found" code.
    assert exc_info.value.code == -32001
    consensus_service.load_contract.assert_called_once_with("Queues")


def test_consensus_service_load_contract_returns_None_when_deployment_missing(
    monkeypatch,
):
    """consensus_service.load_contract must return None — not raise — for
    non-ConsensusMain contracts whose deployment file isn't on disk. This is
    what bypasses the noisy bare-Exception path in rpc_endpoint_manager."""
    from backend.rollup.consensus_service import ConsensusService

    # Avoid the singleton Web3 connection pool grabbing a real chain.
    monkeypatch.setattr(
        "backend.rollup.web3_pool.Web3ConnectionPool.get",
        lambda: MagicMock(),
    )
    svc = ConsensusService()

    # _load_deployment_data returns None when the file is missing — simulate.
    monkeypatch.setattr(svc, "_load_deployment_data", lambda name: None)

    assert svc.load_contract("Queues") is None
    assert svc.load_contract("RevealingPhase") is None
    assert svc.load_contract("IdlenessPhase") is None
