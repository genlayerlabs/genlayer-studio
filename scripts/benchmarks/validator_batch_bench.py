#!/usr/bin/env python3
import argparse
import base64
import inspect
import json
import time
from types import SimpleNamespace


def b64(x: bytes) -> str:
    return base64.b64encode(x).decode("ascii")


def make_snapshot(addr: str, slots: int, value_size: int):
    state = {}
    for i in range(slots):
        slot = i.to_bytes(32, "big")
        seed = i.to_bytes(4, "big")
        value = (seed * ((value_size + 3) // 4))[:value_size]
        state[b64(slot)] = b64(value)
    return SimpleNamespace(contract_address=addr, states={"accepted": state}, balance=0)


def make_view(
    snapshot_cls,
    primary_snapshot,
    snapshot_factory,
    *,
    shared_decode_cache,
    shared_snapshot_cache,
):
    sig = inspect.signature(snapshot_cls.__init__)
    kwargs = {"readonly": True}
    if "shared_decoded_value_cache" in sig.parameters:
        kwargs["shared_decoded_value_cache"] = shared_decode_cache
    if "shared_contract_snapshot_cache" in sig.parameters:
        kwargs["shared_contract_snapshot_cache"] = shared_snapshot_cache
    return snapshot_cls(primary_snapshot, snapshot_factory, **kwargs)


def run_mode(
    mode,
    snapshot_cls,
    address_cls,
    *,
    executions,
    slots_per_contract,
    cross_contracts,
    local_reads,
    cross_reads,
    factory_delay_ms,
):
    primary_addr = ("0x" + "ab" * 20).lower()
    primary_snapshot = make_snapshot(primary_addr, slots_per_contract, 64)
    slots = [i.to_bytes(32, "big") for i in range(slots_per_contract)]

    base_cross = {}
    cross_addrs = []
    for i in range(cross_contracts):
        addr = ("0x" + format(i + 1, "040x")).lower()
        cross_addrs.append(addr)
        base_cross[addr] = make_snapshot(addr, slots_per_contract, 2048)

    primary_address = address_cls(primary_addr)
    cross_addresses = [address_cls(a) for a in cross_addrs]

    # Simulate expensive snapshot creation similar to DB-backed object construction.
    snapshot_factory_calls = {"count": 0}

    def snapshot_factory(addr_hex: str):
        snapshot_factory_calls["count"] += 1
        if factory_delay_ms > 0:
            time.sleep(factory_delay_ms / 1000.0)
        addr_hex = addr_hex.lower()
        src = base_cross[addr_hex]
        return SimpleNamespace(
            contract_address=src.contract_address,
            states={"accepted": dict(src.states["accepted"])},
            balance=src.balance,
        )

    shared_decode_cache = (
        {} if mode in ("shared_decode", "shared_decode_snapshot") else None
    )
    shared_snapshot_cache = {} if mode == "shared_decode_snapshot" else None

    t0 = time.perf_counter()
    for _ in range(executions):
        view = make_view(
            snapshot_cls,
            primary_snapshot,
            snapshot_factory,
            shared_decode_cache=shared_decode_cache,
            shared_snapshot_cache=shared_snapshot_cache,
        )

        local_done = 0
        cross_done = 0
        total_reads = local_reads + cross_reads
        for i in range(total_reads):
            do_cross = (cross_done < cross_reads) and (
                local_done >= local_reads or (i % 5 == 0)
            )
            if do_cross:
                cidx = cross_done % len(cross_addresses)
                sidx = (cross_done * 13) % len(slots)
                view.storage_read(cross_addresses[cidx], slots[sidx], 0, 512)
                cross_done += 1
            else:
                sidx = (local_done * 7) % len(slots)
                view.storage_read(primary_address, slots[sidx], 0, 32)
                local_done += 1

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {
        "mode": mode,
        "executions": executions,
        "elapsed_ms": round(elapsed_ms, 2),
        "snapshot_factory_calls": snapshot_factory_calls["count"],
        "shared_decode_cache_size": (
            len(shared_decode_cache) if shared_decode_cache is not None else 0
        ),
        "shared_snapshot_cache_size": (
            len(shared_snapshot_cache) if shared_snapshot_cache is not None else 0
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executions", type=int, default=6)
    parser.add_argument("--slots", type=int, default=4096)
    parser.add_argument("--cross-contracts", type=int, default=24)
    parser.add_argument("--local-reads", type=int, default=80000)
    parser.add_argument("--cross-reads", type=int, default=30000)
    parser.add_argument("--factory-delay-ms", type=float, default=0.0)
    args = parser.parse_args()

    from backend.node.base import _SnapshotView
    from backend.node.types import Address

    modes = ["none", "shared_decode", "shared_decode_snapshot"]
    results = [
        run_mode(
            m,
            _SnapshotView,
            Address,
            executions=args.executions,
            slots_per_contract=args.slots,
            cross_contracts=args.cross_contracts,
            local_reads=args.local_reads,
            cross_reads=args.cross_reads,
            factory_delay_ms=args.factory_delay_ms,
        )
        for m in modes
    ]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
