import json

import pytest
import requests


@pytest.fixture(scope="session", autouse=True)
def ensure_rate_limiting_disabled():
    """Fail fast if the backend has rate limiting enabled.

    RATE_LIMIT_ENABLED defaults to false, so integration tests run without
    rate limits unless someone explicitly enables it.  This guard prevents
    confusing 429 errors during test runs.
    """
    url = "http://localhost:4000/api"
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
