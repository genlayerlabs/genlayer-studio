import asyncio
import time

import pytest

from backend.node.genvm.base import Host
from backend.node.genvm.origin.public_abi import StorageType


class _SlowStateProxy:
    def storage_read(self, *_args):
        time.sleep(0.1)
        return b"x"

    def get_balance(self, *_args):
        time.sleep(0.1)
        return 0


@pytest.mark.asyncio
async def test_storage_read_runs_off_event_loop():
    host = Host(
        None,
        calldata_bytes=b"",
        state_proxy=_SlowStateProxy(),
        leader_results=None,
    )

    storage_task = asyncio.create_task(
        host.storage_read(
            StorageType.DEFAULT,
            b"\x01" * 20,
            b"\x02" * 32,
            0,
            1,
        )
    )

    start = asyncio.get_running_loop().time()
    await asyncio.sleep(0.01)
    elapsed = asyncio.get_running_loop().time() - start

    assert elapsed < 0.05
    assert await storage_task == b"x"
