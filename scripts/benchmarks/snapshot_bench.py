#!/usr/bin/env python3
import argparse
import base64
import inspect
import json
import time
from collections import defaultdict
from statistics import mean
from types import SimpleNamespace


def b64(x: bytes) -> str:
    return base64.b64encode(x).decode("ascii")


def make_snapshot(addr: str, slots_per_contract: int, value_size: int):
    state = {}
    for i in range(slots_per_contract):
        slot = i.to_bytes(32, "big")
        seed = i.to_bytes(4, "big")
        value = (seed * ((value_size + 3) // 4))[:value_size]
        state[b64(slot)] = b64(value)
    return SimpleNamespace(contract_address=addr, states={"accepted": state}, balance=0)


def make_view(snapshot_cls, primary_snapshot, snapshot_factory, shared_cache):
    sig = inspect.signature(snapshot_cls.__init__)
    kwargs = {"readonly": True}
    if "shared_decoded_value_cache" in sig.parameters:
        kwargs["shared_decoded_value_cache"] = shared_cache
    return snapshot_cls(primary_snapshot, snapshot_factory, **kwargs)


def build_slots(slots_per_contract: int):
    return [i.to_bytes(32, "big") for i in range(slots_per_contract)]


def run_scenario(
    scenario,
    snapshot_cls,
    address_cls,
    executions: int,
    use_shared_cache: bool,
):
    slots = build_slots(scenario["slots_per_contract"])

    primary_addr = ("0x" + "ab" * 20).lower()
    primary_snapshot = make_snapshot(
        primary_addr, scenario["slots_per_contract"], scenario["local_value_size"]
    )

    cross_addrs = []
    cross_snapshots = {}
    for i in range(scenario["cross_contracts"]):
        addr = ("0x" + format(i + 1, "040x")).lower()
        cross_addrs.append(addr)
        cross_snapshots[addr] = make_snapshot(
            addr, scenario["slots_per_contract"], scenario["cross_value_size"]
        )

    snapshot_calls = {"count": 0}

    def snapshot_factory(addr_hex: str):
        snapshot_calls["count"] += 1
        addr_hex = addr_hex.lower()
        if addr_hex == primary_addr:
            return primary_snapshot
        return cross_snapshots[addr_hex]

    primary_address = address_cls(primary_addr)
    cross_addresses = [address_cls(a) for a in cross_addrs]

    shared_cache = {} if use_shared_cache else None
    per_exec_ms = []
    agg_metrics = defaultdict(int)

    local_reads = scenario["local_reads"]
    cross_reads = scenario["cross_reads"]
    total_reads = local_reads + cross_reads
    local_stride = scenario["local_stride"]
    cross_stride = scenario["cross_stride"]

    for _ in range(executions):
        view = make_view(snapshot_cls, primary_snapshot, snapshot_factory, shared_cache)
        t0 = time.perf_counter()

        local_done = 0
        cross_done = 0
        for i in range(total_reads):
            do_cross = (cross_done < cross_reads) and (
                local_done >= local_reads or (i % 5 == 0)
            )
            if do_cross:
                cidx = cross_done % len(cross_addresses) if cross_addresses else 0
                sidx = (cross_done * cross_stride) % len(slots)
                view.storage_read(
                    cross_addresses[cidx], slots[sidx], 0, scenario["cross_read_len"]
                )
                cross_done += 1
            else:
                sidx = (local_done * local_stride) % len(slots)
                view.storage_read(
                    primary_address, slots[sidx], 0, scenario["local_read_len"]
                )
                local_done += 1

        per_exec_ms.append((time.perf_counter() - t0) * 1000)

        if hasattr(view, "get_metrics"):
            metrics = view.get_metrics()
            for k, v in metrics.items():
                if isinstance(v, int):
                    agg_metrics[k] += v

    warm = per_exec_ms[1:] if len(per_exec_ms) > 1 else per_exec_ms
    return {
        "scenario": scenario["name"],
        "use_shared_cache": use_shared_cache,
        "executions": executions,
        "first_exec_ms": round(per_exec_ms[0], 2),
        "warm_mean_ms": round(mean(warm), 2),
        "mean_exec_ms": round(mean(per_exec_ms), 2),
        "min_exec_ms": round(min(per_exec_ms), 2),
        "max_exec_ms": round(max(per_exec_ms), 2),
        "total_ms": round(sum(per_exec_ms), 2),
        "snapshot_factory_calls": snapshot_calls["count"],
        "shared_cache_size": len(shared_cache) if shared_cache is not None else 0,
        "metrics": dict(agg_metrics),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executions", type=int, default=7)
    args = parser.parse_args()

    from backend.node.base import _SnapshotView
    from backend.node.types import Address

    scenarios = [
        {
            "name": "storage_heavy_local_only",
            "slots_per_contract": 4096,
            "cross_contracts": 0,
            "local_reads": 289_000,
            "cross_reads": 0,
            "local_stride": 7,
            "cross_stride": 13,
            "local_read_len": 32,
            "cross_read_len": 32,
            "local_value_size": 64,
            "cross_value_size": 64,
        },
        {
            "name": "mixed_reads_small_cross_payload",
            "slots_per_contract": 4096,
            "cross_contracts": 24,
            "local_reads": 220_000,
            "cross_reads": 69_000,
            "local_stride": 7,
            "cross_stride": 13,
            "local_read_len": 32,
            "cross_read_len": 32,
            "local_value_size": 64,
            "cross_value_size": 64,
        },
        {
            "name": "mixed_reads_large_cross_payload",
            "slots_per_contract": 4096,
            "cross_contracts": 24,
            "local_reads": 220_000,
            "cross_reads": 69_000,
            "local_stride": 7,
            "cross_stride": 13,
            "local_read_len": 32,
            "cross_read_len": 512,
            "local_value_size": 64,
            "cross_value_size": 2048,
        },
    ]

    results = []
    supports_shared = (
        "shared_decoded_value_cache"
        in inspect.signature(_SnapshotView.__init__).parameters
    )
    for scenario in scenarios:
        results.append(
            run_scenario(
                scenario,
                _SnapshotView,
                Address,
                executions=args.executions,
                use_shared_cache=False,
            )
        )
        if supports_shared:
            results.append(
                run_scenario(
                    scenario,
                    _SnapshotView,
                    Address,
                    executions=args.executions,
                    use_shared_cache=True,
                )
            )

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
