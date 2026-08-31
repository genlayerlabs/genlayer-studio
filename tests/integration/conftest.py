import inspect
import json
import os
from functools import wraps

import pytest
import requests
from genlayer_py.abi import calldata
from genlayer_py.abi.transactions import serialize
from genlayer_py.client.genlayer_client import GenLayerClient
from genlayer_py.contracts.utils import make_calldata_object
from web3.constants import ADDRESS_ZERO

from tests.integration import gltest_compat

# gltest 0.24 discovers only the legacy base; remove after the gltest pin moves.
gltest_compat.apply()


def _default_studio_fees(client: GenLayerClient) -> dict | None:
    """Cache the canonical SDK estimate used by fee-enabled integration calls."""
    cache_key = "_studio_integration_default_fees"
    if hasattr(client, cache_key):
        return getattr(client, cache_key)

    estimate = client.estimate_transaction_fees()
    fees = None
    if estimate.get("policy", {}).get("enabled", True):
        fees = {
            "distribution": estimate["distribution"],
            "feeValue": estimate["feeValue"],
        }
    setattr(client, cache_key, fees)
    return fees


def _transaction_fees_from_estimate(estimate: dict) -> dict:
    distribution = estimate.get("distribution")
    fee_value = estimate.get("feeValue", estimate.get("fee_value"))
    if not isinstance(distribution, dict) or fee_value is None:
        raise RuntimeError("fee estimator returned no recommended preset")
    fees = {
        "distribution": distribution,
        "feeValue": fee_value,
    }
    allocations = estimate.get(
        "messageAllocations", estimate.get("message_allocations")
    )
    if allocations is not None:
        fees["messageAllocations"] = allocations
    return fees


def _is_expected_simulation_execution_failure(error: Exception) -> bool:
    return "execution failed" in str(error).lower()


def _estimate_deploy_fees(
    client: GenLayerClient,
    *,
    code,
    account=None,
    args=None,
    kwargs=None,
    leader_only=False,
    sim_config=None,
) -> dict:
    sender = account or client.local_account
    data = serialize(
        [
            code,
            calldata.encode(
                make_calldata_object(method=None, args=args, kwargs=kwargs)
            ),
            leader_only,
        ]
    )
    request = {
        "type": "deploy",
        "to": ADDRESS_ZERO,
        "from": sender.address,
        "data": data,
    }
    if sim_config is not None:
        request["sim_config"] = sim_config
    response = client.provider.make_request(
        method="sim_estimateTransactionFees",
        params=[request],
    )
    if response.get("error"):
        raise RuntimeError(response["error"].get("message", "fee estimate failed"))
    preset = (response.get("result") or {}).get("recommendedPreset") or {}
    return _transaction_fees_from_estimate(preset)


@pytest.fixture(scope="session", autouse=True)
def use_fee_aware_sdk_defaults():
    """Exercise fee-enabled Studio through the train SDK's canonical quote.

    Individual tests can still supply an explicit fee shape (including an
    intentionally invalid one). Gasless CI keeps the legacy zero-fee call.
    """
    original_deploy = GenLayerClient.deploy_contract
    original_write = GenLayerClient.write_contract

    @wraps(original_deploy)
    def deploy_with_default_fees(client, *args, **call_kwargs):
        if call_kwargs.get("fees") is None:
            fees = None
            default_fees = _default_studio_fees(client)
            if default_fees is not None:
                try:
                    bound = inspect.signature(original_deploy).bind(
                        client, *args, **call_kwargs
                    )
                    bound.apply_defaults()
                    fees = _estimate_deploy_fees(
                        client,
                        code=bound.arguments["code"],
                        account=bound.arguments["account"],
                        args=bound.arguments["args"],
                        kwargs=bound.arguments["kwargs"],
                        leader_only=bound.arguments["leader_only"],
                        sim_config=bound.arguments["sim_config"],
                    )
                except Exception as error:
                    # Error-path tests still need to submit the transaction so
                    # they can assert its finalized execution failure.
                    if not _is_expected_simulation_execution_failure(error):
                        raise
                    fees = default_fees
            if fees is not None:
                call_kwargs["fees"] = fees
        return original_deploy(client, *args, **call_kwargs)

    @wraps(original_write)
    def write_with_default_fees(client, *args, **call_kwargs):
        if call_kwargs.get("fees") is None:
            fees = None
            default_fees = _default_studio_fees(client)
            if default_fees is not None:
                try:
                    bound = inspect.signature(original_write).bind(
                        client, *args, **call_kwargs
                    )
                    bound.apply_defaults()
                    estimate = client.estimate_transaction_fees_for_write(
                        address=bound.arguments["address"],
                        function_name=bound.arguments["function_name"],
                        account=bound.arguments["account"],
                        args=bound.arguments["args"],
                        kwargs=bound.arguments["kwargs"],
                        value=bound.arguments["value"],
                        leader_only=bound.arguments["leader_only"],
                        sim_config=bound.arguments["sim_config"],
                    )
                    fees = _transaction_fees_from_estimate(estimate)
                except Exception as error:
                    if not _is_expected_simulation_execution_failure(error):
                        raise
                    fees = default_fees
            if fees is not None:
                call_kwargs["fees"] = fees
        return original_write(client, *args, **call_kwargs)

    patch = pytest.MonkeyPatch()
    patch.setattr(GenLayerClient, "deploy_contract", deploy_with_default_fees)
    patch.setattr(GenLayerClient, "write_contract", write_with_default_fees)
    yield
    patch.undo()


@pytest.fixture(scope="session", autouse=True)
def ensure_rate_limiting_disabled():
    """Fail fast if the backend has rate limiting enabled.

    RATE_LIMIT_ENABLED defaults to false, so integration tests run without
    rate limits unless someone explicitly enables it.  This guard prevents
    confusing 429 errors during test runs.
    """
    url = os.environ.get("TEST_JSONRPC_URL", "http://localhost:4000/api")
    # Send a burst of rapid requests — if we get a 429, rate limiting is on.
    for _ in range(15):
        resp = requests.post(
            url,
            data=json.dumps(
                {"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}
            ),
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 429:
            pytest.exit(
                "Rate limiting is enabled on the backend. "
                "Set RATE_LIMIT_ENABLED=false in .env and restart containers "
                "before running integration tests.",
                returncode=1,
            )
