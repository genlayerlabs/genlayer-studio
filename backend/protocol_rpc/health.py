# backend/protocol_rpc/health.py
import time
import os
from typing import Optional, Union, Dict, Any
import logging
import asyncio

from fastapi import APIRouter, FastAPI, Depends
from backend.database_handler.session_factory import get_database_manager
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.dependencies import get_rpc_router_optional

# Create FastAPI router for health endpoints
health_router = APIRouter(tags=["health"])


@health_router.get("/health")
async def health_check(
    rpc_router: Optional[FastAPIRPCRouter] = Depends(get_rpc_router_optional),
) -> dict:
    """Unified health check endpoint summarizing all system metrics by calling other endpoints."""
    start = time.time()
    overall_status = "healthy"
    issues = []

    # Call existing health endpoints to reuse logic
    try:
        # 1. Database health
        db_health = await health_database()
        db_status = db_health.get("status", "unknown")
        if db_status in ["unhealthy", "error"]:
            overall_status = "unhealthy"
            issues.append("database_issue")
        elif db_status == "degraded":
            if overall_status == "healthy":
                overall_status = "degraded"

        # 2. Workers health
        workers_health = await health_workers()
        workers_status = workers_health.get("status", "unknown")
        workers_summary = {
            "total": workers_health.get("total_workers", 0),
            "healthy": workers_health.get("healthy_workers", 0),
            "status": workers_status,
        }
        if workers_status in ["unhealthy", "error"]:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("workers_unhealthy")
        elif workers_health.get("total_workers", 0) == 0:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("no_consensus_workers")

        # Add jsonrpc stats if available
        jsonrpc_summary = None
        if workers_health.get("jsonrpc"):
            jsonrpc_summary = {
                "status": workers_health["jsonrpc"].get("status"),
                "cpu_percent": workers_health["jsonrpc"].get("cpu_percent"),
                "memory_mb": workers_health["jsonrpc"].get("memory_mb"),
                "memory_percent": workers_health["jsonrpc"].get("memory_percent"),
            }

        # 3. Consensus health
        consensus_health = await health_consensus(rpc_router)
        consensus_status = consensus_health.get("status", "unknown")
        consensus_summary = {
            "processing_transactions": consensus_health.get(
                "total_processing_transactions", 0
            ),
            "orphaned_transactions": consensus_health.get(
                "total_orphaned_transactions", 0
            ),
            "active_workers": consensus_health.get("active_workers", 0),
            "status": consensus_status,
        }
        if consensus_status in ["unhealthy", "error"]:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("consensus_issue")
        elif consensus_health.get("total_orphaned_transactions", 0) > 0:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("orphaned_transactions")

        # 4. Memory health
        memory_health = await health_memory()
        memory_status = memory_health.get("status", "unknown")
        if memory_status in ["unhealthy", "degraded"]:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("memory_issue")

        # 5. Check Redis (lightweight check not in other endpoints)
        redis_status = "not_configured"
        if os.getenv("REDIS_URL"):
            try:
                import redis

                redis_client = redis.from_url(os.getenv("REDIS_URL"))
                redis_client.ping()
                redis_status = "healthy"
            except Exception:
                redis_status = "unhealthy"
                if overall_status == "healthy":
                    overall_status = "degraded"
                issues.append("redis_unreachable")

        return {
            "status": overall_status,
            "timestamp": time.time(),
            "response_time_ms": round((time.time() - start) * 1000, 2),
            "issues": issues if issues else None,
            "services": {
                "database": {
                    "status": db_status,
                    "pool_size": db_health.get("pool", {}).get("size"),
                    "checked_out": db_health.get("pool", {}).get("checked_out"),
                },
                "redis": redis_status,
                "jsonrpc": jsonrpc_summary,
                "consensus_workers": workers_summary,
                "consensus": consensus_summary,
                "memory": {
                    "status": memory_status,
                    "usage_mb": memory_health.get("memory_usage_mb"),
                    "percent": memory_health.get("memory_percent"),
                },
            },
            "meta": {
                "pid": os.getpid(),
                "workers": os.getenv("WEB_CONCURRENCY", "1"),
            },
        }

    except Exception as e:
        logging.exception("Health check failed")
        return {
            "status": "error",
            "timestamp": time.time(),
            "response_time_ms": round((time.time() - start) * 1000, 2),
            "error": str(e),
        }


@health_router.get("/ready")
async def readiness_check():
    """Readiness check to verify the service is ready to accept traffic."""
    return {
        "status": "ready",
        "service": "genlayer-rpc",
    }


def create_readiness_check_with_state(
    source: Union[FastAPI, Optional[FastAPIRPCRouter]],
):
    """Create a readiness check function that evaluates RPC router availability."""

    if isinstance(source, FastAPI):

        def rpc_router_provider() -> Optional[FastAPIRPCRouter]:
            return getattr(source.state, "rpc_router", None)

    else:

        def rpc_router_provider() -> Optional[FastAPIRPCRouter]:
            return source

    async def readiness_check_with_state():
        """Readiness check to verify the service is ready to accept traffic."""
        rpc_router_ready = rpc_router_provider() is not None

        return {
            "status": "ready" if rpc_router_ready else "not_ready",
            "service": "genlayer-rpc",
            "rpc_router_initialized": rpc_router_ready,
        }

    return readiness_check_with_state


@health_router.get("/health/tasks")
async def health_tasks() -> Dict[str, Any]:
    """Show status of background tasks and monitoring information."""
    try:
        from backend.consensus.monitoring import get_monitor

        monitor = get_monitor()
        status = monitor.get_status()

        # Add task health assessment
        all_healthy = True
        if status.get("stale_tasks"):
            all_healthy = False

        task_health = "healthy" if all_healthy else "degraded"

        return {
            "status": task_health,
            "uptime_seconds": status.get("uptime_seconds", 0),
            "active_tasks": status.get("active_tasks", 0),
            "stale_tasks": len(status.get("stale_tasks", [])),
            "stale_task_details": status.get("stale_tasks", []),
            "tasks": status.get("tasks", {}),
            "memory_usage_mb": status.get("memory_usage_mb", 0),
            "cpu_percent": status.get("cpu_percent", 0),
        }
    except Exception as e:
        logging.exception("Task health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/db")
async def health_database() -> Dict[str, Any]:
    """Show database connection pool statistics and session tracking."""
    try:
        from backend.consensus.monitoring import get_monitor
        from sqlalchemy import text

        monitor = get_monitor()
        status = monitor.get_status()

        db_manager = get_database_manager()
        pool_status = {}

        # Get connection pool stats if available
        try:
            pool = db_manager.engine.pool
            pool_status = {"class": pool.__class__.__name__}

            # Try to get pool statistics based on pool type
            if hasattr(pool, "status"):
                # Some pools have a status() method
                try:
                    pool_status["status"] = pool.status()
                except:
                    pass

            # Try common pool attributes
            if hasattr(pool, "size"):
                try:
                    pool_status["size"] = pool.size()
                except:
                    pass

            if hasattr(pool, "checkedout"):
                try:
                    pool_status["checked_out"] = pool.checkedout()
                except:
                    pass

            if hasattr(pool, "overflow"):
                try:
                    pool_status["overflow"] = pool.overflow()
                except:
                    pass

            # Calculate total if we have the components
            if "checked_out" in pool_status and "overflow" in pool_status:
                pool_status["total"] = (
                    pool_status["checked_out"] + pool_status["overflow"]
                )

            # For QueuePool specifically, try to get more info
            if pool.__class__.__name__ == "QueuePool":
                if hasattr(pool, "_pool"):
                    # Internal pool queue
                    try:
                        pool_status["available"] = (
                            pool._pool.qsize() if hasattr(pool._pool, "qsize") else None
                        )
                    except:
                        pass

        except Exception as e:
            logging.debug(f"Could not get pool statistics: {e}")
            pool_status = {"class": "unknown", "error": str(e)}

        # Test database connectivity
        db_healthy = False
        query_time_ms = 0
        try:
            start = time.time()
            with db_manager.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            query_time_ms = (time.time() - start) * 1000
            db_healthy = True
        except Exception as e:
            logging.error(f"Database connectivity test failed: {e}")

        return {
            "status": "healthy" if db_healthy else "unhealthy",
            "active_sessions": status.get("active_sessions", 0),
            "connection_pool": pool_status,
            "query_time_ms": query_time_ms,
            "database_url": (
                db_manager.engine.url.render_as_string(hide_password=True)
                if hasattr(db_manager, "engine")
                else "unknown"
            ),
        }
    except Exception as e:
        logging.exception("Database health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/processing")
async def health_processing() -> Dict[str, Any]:
    """Show current transaction processing status."""
    try:
        from backend.consensus.monitoring import get_monitor

        monitor = get_monitor()
        status = monitor.get_status()

        processing = status.get("processing", {})
        processing_count = status.get("processing_transactions", 0)

        return {
            "status": "healthy",
            "processing_count": processing_count,
            "processing_transactions": processing,
            "contracts_being_processed": list(processing.keys()) if processing else [],
        }
    except Exception as e:
        logging.exception("Processing health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/memory")
async def health_memory() -> Dict[str, Any]:
    """Show detailed memory usage statistics."""
    try:
        import psutil
        import gc

        process = psutil.Process()
        memory_info = process.memory_info()

        # Get garbage collection stats
        gc_stats = gc.get_stats()

        return {
            "status": "healthy",
            "memory_usage_mb": memory_info.rss / 1024 / 1024,
            "memory_percent": process.memory_percent(),
            "virtual_memory_mb": (
                memory_info.vms / 1024 / 1024 if hasattr(memory_info, "vms") else 0
            ),
            "gc_objects": len(gc.get_objects()),
            "gc_stats": gc_stats[0] if gc_stats else {},
            "system_memory": {
                "total_mb": psutil.virtual_memory().total / 1024 / 1024,
                "available_mb": psutil.virtual_memory().available / 1024 / 1024,
                "percent_used": psutil.virtual_memory().percent,
            },
        }
    except Exception as e:
        logging.exception("Memory health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/consensus")
async def health_consensus(
    rpc_router: Optional[FastAPIRPCRouter] = Depends(get_rpc_router_optional),
) -> Dict[str, Any]:
    """Show consensus system status with detailed contract-level transaction metrics."""
    import subprocess
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func, and_, or_
    from backend.database_handler.models import Transactions, TransactionStatus
    from backend.database_handler.session_factory import get_database_manager

    try:
        if not rpc_router:
            return {"status": "not_initialized", "error": "RPC router not available"}

        # Get active worker IDs from running containers
        ps_result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.service=consensus-worker",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        active_workers = set()
        if ps_result.returncode == 0:
            container_names = [
                name.strip()
                for name in ps_result.stdout.strip().split("\n")
                if name.strip()
            ]

            # Get worker IDs by querying each container's health endpoint
            import aiohttp
            import asyncio

            async def get_worker_id(container_name: str) -> Optional[str]:
                try:
                    ip_result = subprocess.run(
                        [
                            "docker",
                            "inspect",
                            "-f",
                            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                            container_name,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if ip_result.returncode == 0:
                        ip = ip_result.stdout.strip()
                        if ip:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(
                                    f"http://{ip}:4001/health",
                                    timeout=aiohttp.ClientTimeout(total=2),
                                ) as resp:
                                    if resp.status == 200:
                                        data = await resp.json()
                                        return data.get("worker_id")
                except:
                    pass
                return None

            worker_ids = await asyncio.gather(
                *[get_worker_id(name) for name in container_names]
            )
            active_workers = {wid for wid in worker_ids if wid}

        # Query transaction statistics by contract
        db_manager = get_database_manager()
        with db_manager.engine.connect() as conn:
            now = datetime.now(timezone.utc)

            # Define processing statuses
            processing_statuses = [
                TransactionStatus.PENDING,
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
                TransactionStatus.UNDETERMINED,
            ]

            # Get contract-level statistics
            from sqlalchemy import select, text

            query = text(
                """
                SELECT
                    to_address as contract_address,
                    COUNT(*) FILTER (WHERE status IN ('ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) as processing_count,
                    COUNT(*) FILTER (WHERE status = 'PENDING') as pending_count,
                    COUNT(*) FILTER (WHERE created_at > :one_hour_ago) as created_last_1h,
                    COUNT(*) FILTER (WHERE created_at > :three_hours_ago) as created_last_3h,
                    COUNT(*) FILTER (WHERE created_at > :six_hours_ago) as created_last_6h,
                    COUNT(*) FILTER (WHERE created_at > :twelve_hours_ago) as created_last_12h,
                    COUNT(*) FILTER (WHERE created_at > :one_day_ago) as created_last_1d,
                    MIN(blocked_at) as oldest_blocked_at,
                    COUNT(*) FILTER (WHERE worker_id IS NOT NULL AND status IN ('PENDING', 'ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) as blocked_count,
                    json_agg(DISTINCT jsonb_build_object('worker_id', worker_id, 'hash', hash))
                        FILTER (WHERE worker_id IS NOT NULL AND status IN ('PENDING', 'ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) as worker_transactions
                FROM transactions
                WHERE to_address IS NOT NULL
                GROUP BY to_address
                HAVING COUNT(*) FILTER (WHERE status IN ('PENDING', 'ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) > 0
                ORDER BY processing_count DESC
            """
            )

            result = conn.execute(
                query,
                {
                    "one_hour_ago": now - timedelta(hours=1),
                    "three_hours_ago": now - timedelta(hours=3),
                    "six_hours_ago": now - timedelta(hours=6),
                    "twelve_hours_ago": now - timedelta(hours=12),
                    "one_day_ago": now - timedelta(days=1),
                },
            )

            contracts = []
            total_orphaned = 0

            for row in result:
                contract_data = {
                    "contract_address": row.contract_address,
                    "processing_count": row.processing_count,
                    "pending_count": row.pending_count,
                    "created_last_1h": row.created_last_1h,
                    "created_last_3h": row.created_last_3h,
                    "created_last_6h": row.created_last_6h,
                    "created_last_12h": row.created_last_12h,
                    "created_last_1d": row.created_last_1d,
                }

                # Calculate elapsed time for oldest transaction
                if row.oldest_blocked_at:
                    elapsed = now - row.oldest_blocked_at
                    minutes = int(elapsed.total_seconds() / 60)
                    if minutes < 60:
                        contract_data["oldest_transaction_elapsed"] = f"{minutes}m"
                    else:
                        hours = minutes // 60
                        contract_data["oldest_transaction_elapsed"] = f"{hours}h"
                else:
                    contract_data["oldest_transaction_elapsed"] = None

                # Detect orphaned transactions
                orphaned_tx_hashes = []
                if row.worker_transactions:
                    for tx_info in row.worker_transactions:
                        if tx_info and tx_info.get("worker_id") not in active_workers:
                            orphaned_tx_hashes.append(tx_info.get("hash"))

                contract_data["orphaned_transactions"] = len(orphaned_tx_hashes)
                if orphaned_tx_hashes:
                    contract_data["orphaned_transaction_hashes"] = orphaned_tx_hashes
                total_orphaned += contract_data["orphaned_transactions"]

                contracts.append(contract_data)

            # Overall status
            total_processing = sum(c["processing_count"] for c in contracts)
            status = (
                "healthy"
                if total_processing < 100 and total_orphaned == 0
                else "degraded"
            )

            return {
                "status": status,
                "total_processing_transactions": total_processing,
                "total_orphaned_transactions": total_orphaned,
                "active_workers": len(active_workers),
                "contracts": contracts,
            }

    except Exception as e:
        logging.exception("Consensus health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/workers")
async def health_workers() -> Dict[str, Any]:
    """Aggregate health status from all consensus-worker containers using docker CLI."""
    import subprocess
    import asyncio
    import aiohttp
    import re

    try:
        # Get list of consensus-worker containers using docker ps
        ps_result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.service=consensus-worker",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if ps_result.returncode != 0:
            return {
                "status": "error",
                "error": f"Failed to list containers: {ps_result.stderr}",
                "workers": [],
            }

        container_names = [
            name.strip()
            for name in ps_result.stdout.strip().split("\n")
            if name.strip()
        ]

        if not container_names:
            return {
                "status": "error",
                "error": "No consensus-worker containers found",
                "workers": [],
            }

        # Get stats for all containers in one command
        stats_result = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.Container}}",
            ]
            + container_names,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if stats_result.returncode != 0:
            return {
                "status": "error",
                "error": f"Failed to get container stats: {stats_result.stderr}",
                "workers": [],
            }

        # Parse stats output
        stats_by_name = {}
        for line in stats_result.stdout.strip().split("\n"):
            if line.strip():
                parts = line.split("\t")
                if len(parts) >= 4:
                    name, cpu, mem, container_id = (
                        parts[0],
                        parts[1],
                        parts[2],
                        parts[3],
                    )
                    # Parse CPU (e.g., "0.50%" -> 0.50)
                    cpu_match = re.search(r"([\d.]+)%", cpu)
                    cpu_percent = float(cpu_match.group(1)) if cpu_match else 0.0

                    # Parse memory (e.g., "123.4MiB / 2GiB" -> 123.4)
                    mem_match = re.search(r"([\d.]+)([KMGT]i?B)", mem)
                    memory_mb = 0.0
                    if mem_match:
                        value = float(mem_match.group(1))
                        unit = mem_match.group(2)
                        if "G" in unit:
                            memory_mb = value * 1024
                        elif "M" in unit:
                            memory_mb = value
                        elif "K" in unit:
                            memory_mb = value / 1024

                    stats_by_name[name] = {
                        "container_id": container_id[:12],
                        "cpu_percent": round(cpu_percent, 2),
                        "memory_mb": round(memory_mb, 2),
                    }

        # Fetch health status from each worker in parallel
        async def fetch_worker_health(
            container_name: str, stats: Dict
        ) -> Dict[str, Any]:
            """Fetch health status from a single worker."""
            try:
                # Get container IP
                ip_result = subprocess.run(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                        container_name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                if ip_result.returncode != 0:
                    return {
                        "container_name": container_name,
                        "status": "error",
                        "error": "Failed to get IP address",
                        "cpu_percent": stats.get("cpu_percent", 0),
                        "memory_mb": stats.get("memory_mb", 0),
                        "memory_percent": 0,
                    }

                ip_address = ip_result.stdout.strip()
                if not ip_address:
                    return {
                        "container_name": container_name,
                        "status": "error",
                        "error": "No IP address found",
                        "cpu_percent": stats.get("cpu_percent", 0),
                        "memory_mb": stats.get("memory_mb", 0),
                        "memory_percent": 0,
                    }

                # Query the worker's health endpoint
                url = f"http://{ip_address}:4001/health"

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as response:
                        if response.status == 200:
                            data = await response.json()

                            # Build simplified response
                            result = {
                                "container_name": container_name,
                                "status": data.get("status", "unknown"),
                                "worker_id": data.get("worker_id"),
                                "cpu_percent": stats.get("cpu_percent", 0),
                                "memory_mb": stats.get("memory_mb", 0),
                                "memory_percent": data.get("memory_percent", 0),
                            }

                            # Add transaction being processed if available
                            if data.get("current_transaction"):
                                tx = data["current_transaction"]
                                result["current_transaction"] = {
                                    "hash": tx.get("hash"),
                                    "blocked_at": tx.get("blocked_at"),
                                }

                            return result
                        else:
                            return {
                                "container_name": container_name,
                                "status": "unhealthy",
                                "error": f"HTTP {response.status}",
                                "cpu_percent": stats.get("cpu_percent", 0),
                                "memory_mb": stats.get("memory_mb", 0),
                                "memory_percent": 0,
                            }
            except asyncio.TimeoutError:
                return {
                    "container_name": container_name,
                    "status": "timeout",
                    "error": "Health check timeout",
                    "cpu_percent": stats.get("cpu_percent", 0),
                    "memory_mb": stats.get("memory_mb", 0),
                    "memory_percent": 0,
                }
            except Exception as e:
                return {
                    "container_name": container_name,
                    "status": "error",
                    "error": str(e),
                    "cpu_percent": stats.get("cpu_percent", 0),
                    "memory_mb": stats.get("memory_mb", 0),
                    "memory_percent": 0,
                }

        # Fetch health from all workers in parallel
        workers_health = await asyncio.gather(
            *[
                fetch_worker_health(name, stats_by_name.get(name, {}))
                for name in container_names
            ]
        )

        # Get jsonrpc container stats
        jsonrpc_stats = None
        jsonrpc_container_name = None
        try:
            # Find jsonrpc container
            jsonrpc_ps_result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    "label=com.docker.compose.service=jsonrpc",
                    "--format",
                    "{{.Names}}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if jsonrpc_ps_result.returncode == 0:
                jsonrpc_names = [
                    name.strip()
                    for name in jsonrpc_ps_result.stdout.strip().split("\n")
                    if name.strip()
                ]
                if jsonrpc_names:
                    jsonrpc_container_name = jsonrpc_names[0]

                    # Get stats for jsonrpc container
                    jsonrpc_stats_result = subprocess.run(
                        [
                            "docker",
                            "stats",
                            "--no-stream",
                            "--format",
                            "{{.CPUPerc}}\t{{.MemUsage}}",
                            jsonrpc_container_name,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )

                    if jsonrpc_stats_result.returncode == 0:
                        output = jsonrpc_stats_result.stdout.strip()
                        if output:
                            parts = output.split("\t")
                            if len(parts) >= 2:
                                cpu, mem = parts[0], parts[1]

                                # Parse CPU
                                cpu_match = re.search(r"([\d.]+)%", cpu)
                                cpu_percent = (
                                    float(cpu_match.group(1)) if cpu_match else 0.0
                                )

                                # Parse memory - extract both used and total
                                mem_match = re.search(
                                    r"([\d.]+)([KMGT]i?B)\s*/\s*([\d.]+)([KMGT]i?B)",
                                    mem,
                                )
                                memory_mb = 0.0
                                total_memory_mb = 0.0
                                memory_percent = 0.0

                                if mem_match:
                                    # Used memory
                                    used_value = float(mem_match.group(1))
                                    used_unit = mem_match.group(2)
                                    if "G" in used_unit:
                                        memory_mb = used_value * 1024
                                    elif "M" in used_unit:
                                        memory_mb = used_value
                                    elif "K" in used_unit:
                                        memory_mb = used_value / 1024

                                    # Total memory
                                    total_value = float(mem_match.group(3))
                                    total_unit = mem_match.group(4)
                                    if "G" in total_unit:
                                        total_memory_mb = total_value * 1024
                                    elif "M" in total_unit:
                                        total_memory_mb = total_value
                                    elif "K" in total_unit:
                                        total_memory_mb = total_value / 1024

                                    # Calculate percentage
                                    if total_memory_mb > 0:
                                        memory_percent = (
                                            memory_mb / total_memory_mb
                                        ) * 100

                                jsonrpc_stats = {
                                    "container_name": jsonrpc_container_name,
                                    "status": "healthy",
                                    "cpu_percent": round(cpu_percent, 2),
                                    "memory_mb": round(memory_mb, 2),
                                    "memory_percent": round(memory_percent, 2),
                                }
        except Exception as e:
            logging.warning(f"Failed to get jsonrpc stats: {e}")

        # Determine overall status
        all_healthy = all(w.get("status") == "healthy" for w in workers_health)
        any_unhealthy = any(
            w.get("status") in ["unhealthy", "failed"] for w in workers_health
        )

        overall_status = (
            "healthy" if all_healthy else ("unhealthy" if any_unhealthy else "degraded")
        )

        # Aggregate metrics
        total_memory_mb = sum(w.get("memory_mb", 0) for w in workers_health)
        avg_cpu_percent = (
            sum(w.get("cpu_percent", 0) for w in workers_health) / len(workers_health)
            if workers_health
            else 0
        )

        result = {
            "status": overall_status,
            "total_workers": len(container_names),
            "healthy_workers": sum(
                1 for w in workers_health if w.get("status") == "healthy"
            ),
            "workers": workers_health,
            "aggregated_metrics": {
                "total_memory_mb": round(total_memory_mb, 2),
                "avg_cpu_percent": round(avg_cpu_percent, 2),
            },
        }

        # Add jsonrpc stats if available
        if jsonrpc_stats:
            result["jsonrpc"] = jsonrpc_stats

        return result

    except subprocess.TimeoutExpired:
        logging.exception("Docker command timeout in worker health check")
        return {"status": "error", "error": "Docker command timeout", "workers": []}
    except Exception as e:
        logging.exception("Worker health check failed")
        return {"status": "error", "error": str(e), "workers": []}
