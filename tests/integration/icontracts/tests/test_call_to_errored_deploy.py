"""
Regression: a deploy tx that finalizes with execution_result=ERROR must not
leave a "zombie" contract at its address.

Bug: today, the deploy failure still creates a current_state row for the
target address. Subsequent calls to that address don't raise
ContractNotFoundError — they hang in the consensus loop until the
recovery-cycle cap cancels them.

Expected: deploy failure should leave the address undeployed, and calls
to it should finalize with execution_result=ERROR (FinishedWithError)
within normal consensus time.
"""

from gltest import get_contract_factory
from gltest.assertions import tx_execution_failed
from gltest.clients import get_gl_client
from gltest.utils import extract_contract_address
from genlayer_py.types import TransactionStatus
from backend.node.types import ExecutionResultStatus
import pytest

pytestmark = pytest.mark.error_handling


def test_call_to_errored_deploy_address_finalizes_with_error(setup_validators):
    setup_validators()
    factory = get_contract_factory("ErrorExecutionContract")

    # Deploy with testcase=5 → __init__ raises ValueError.
    # deploy_contract_tx returns the receipt on exec-failure (it only wraps
    # unexpected RPC exceptions as DeploymentError).
    deploy_receipt = factory.deploy_contract_tx(args=[5])
    assert tx_execution_failed(
        deploy_receipt
    ), "Precondition: deploy is expected to fail with execution_result=ERROR"

    errored_address = extract_contract_address(deploy_receipt)

    # Now send a call to that address. The contract was never successfully
    # deployed, so this should finalize with a contract-not-found error.
    client = get_gl_client()
    call_tx_hash = client.write_contract(
        address=errored_address,
        function_name="test_value_error",
        args=[],
    )

    # Bounded wait: if the bug is present, the tx hangs and never finalizes
    # within this window (it either stays PROPOSING/COMMITTING or gets
    # canceled much later via the recovery-cycle cap). A correct
    # implementation finalizes in normal consensus time. wait_for_transaction_receipt
    # raises GenLayerError on timeout, so reaching this assert means FINALIZED.
    call_receipt = client.wait_for_transaction_receipt(
        transaction_hash=call_tx_hash,
        status=TransactionStatus.FINALIZED,
        interval=5000,  # ms
        retries=60,  # ≈ 5 min ceiling
    )

    # Execution result must be ERROR, synthesized by the worker's
    # ContractNotFoundError handler. Identified by the pseudo-validator address
    # "contract_not_found_handler", which is the handler's distinctive marker
    # (see backend/consensus/worker.py, ContractNotFoundError branch of
    # process_transaction).
    consensus_data = call_receipt.get("consensus_data") or {}
    leader_receipts = consensus_data.get("leader_receipt") or []
    assert (
        leader_receipts
    ), f"receipt must carry a leader_receipt with the error: {call_receipt}"

    leader = leader_receipts[0]
    assert (
        leader.get("execution_result") == ExecutionResultStatus.ERROR.value
    ), f"execution_result must be ERROR, got {leader.get('execution_result')}"

    handler_marker = (leader.get("node_config") or {}).get("address")
    assert handler_marker == "contract_not_found_handler", (
        "leader receipt must come from the contract-not-found handler, "
        f"got node_config.address={handler_marker!r}; receipt={call_receipt}"
    )
