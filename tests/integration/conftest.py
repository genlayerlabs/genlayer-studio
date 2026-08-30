import json
import os
from functools import wraps

import pytest
import requests
from genlayer_py.client.genlayer_client import GenLayerClient

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


@pytest.fixture(scope="session", autouse=True)
def use_fee_aware_sdk_defaults():
    """Exercise fee-enabled Studio through the train SDK's canonical quote.

    Individual tests can still supply an explicit fee shape (including an
    intentionally invalid one). Gasless CI keeps the legacy zero-fee call.
    """
    original_deploy = GenLayerClient.deploy_contract
    original_write = GenLayerClient.write_contract

    @wraps(original_deploy)
    def deploy_with_default_fees(client, *args, **kwargs):
        if kwargs.get("fees") is None:
            fees = _default_studio_fees(client)
            if fees is not None:
                kwargs["fees"] = fees
        return original_deploy(client, *args, **kwargs)

    @wraps(original_write)
    def write_with_default_fees(client, *args, **kwargs):
        if kwargs.get("fees") is None:
            fees = _default_studio_fees(client)
            if fees is not None:
                kwargs["fees"] = fees
        return original_write(client, *args, **kwargs)

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
