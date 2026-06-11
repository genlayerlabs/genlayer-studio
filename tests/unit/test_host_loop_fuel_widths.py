import asyncio
import socket

import pytest

from backend.node.genvm.origin import base_host, host_fns
from tests.unit.test_genvm_retry_integration import RecordingCtx


class FuelHandler:
    def __init__(self, sock, remaining_fuel):
        self.sock = sock
        self.remaining_fuel = remaining_fuel
        self.consumed_gas = []

    async def loop_enter(self, _cancellation):
        return self.sock

    async def consume_gas(self, gas):
        self.consumed_gas.append(gas)

    async def remaining_fuel_as_gen(self):
        return self.remaining_fuel

    async def storage_read(self, *_args):
        raise AssertionError("storage_read should not be called")

    async def eth_call(self, *_args):
        raise AssertionError("eth_call should not be called")

    async def get_balance(self, *_args):
        raise AssertionError("get_balance should not be called")

    async def notify_nondet_disagreement(self, *_args):
        raise AssertionError("notify_nondet_disagreement should not be called")


async def _recv_exact(sock, size):
    loop = asyncio.get_running_loop()
    chunks = bytearray()
    while len(chunks) < size:
        chunk = await loop.sock_recv(sock, size - len(chunks))
        if chunk == b"":
            raise ConnectionResetError()
        chunks.extend(chunk)
    return bytes(chunks)


@pytest.mark.asyncio
async def test_consume_fuel_reads_32_byte_little_endian_u256():
    server, client = socket.socketpair()
    server.setblocking(False)
    client.setblocking(False)
    try:
        gas = (1 << 100) + 7
        client.sendall(
            bytes([host_fns.Methods.CONSUME_FUEL])
            + gas.to_bytes(32, "little")
            + bytes([host_fns.Methods.NOTIFY_FINISHED])
        )

        handler = FuelHandler(server, remaining_fuel=12345)
        await base_host.host_loop(handler, asyncio.Event(), ctx=RecordingCtx())

        assert handler.consumed_gas == [gas]
        assert await _recv_exact(client, 1) == b"\x00"
    finally:
        client.close()
        server.close()


@pytest.mark.asyncio
async def test_remaining_fuel_as_gen_replies_with_32_byte_little_endian_u256():
    server, client = socket.socketpair()
    server.setblocking(False)
    client.setblocking(False)
    try:
        remaining_fuel = 12345
        client.sendall(
            bytes(
                [
                    host_fns.Methods.REMAINING_FUEL_AS_GEN,
                    host_fns.Methods.NOTIFY_FINISHED,
                ]
            )
        )

        handler = FuelHandler(server, remaining_fuel=remaining_fuel)
        await base_host.host_loop(handler, asyncio.Event(), ctx=RecordingCtx())

        assert await _recv_exact(client, 34) == (
            bytes([host_fns.Errors.OK])
            + remaining_fuel.to_bytes(32, "little")
            + b"\x00"
        )
    finally:
        client.close()
        server.close()
