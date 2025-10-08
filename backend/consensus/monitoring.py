"""
Monitoring utilities for consensus background tasks and resource tracking.
Provides heartbeat logging, resource monitoring, and diagnostic tools.
"""

import asyncio
import time
import psutil
import threading
from typing import Dict, Any, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger
from contextlib import asynccontextmanager, contextmanager


@dataclass
class TaskInfo:
    """Information about a running task."""

    name: str
    contract_address: Optional[str]
    start_time: float
    last_heartbeat: float
    iteration_count: int = 0
    errors_count: int = 0
    last_error: Optional[str] = None


class ConsensusMonitor:
    """
    Centralized monitoring for consensus background tasks.
    Tracks task health, resource usage, and provides diagnostic information.
    """

    def __init__(self):
        self.tasks: Dict[str, TaskInfo] = {}
        self.active_sessions: Set[int] = set()
        self.processing_transactions: Dict[str, str] = {}  # contract -> tx_hash
        self.start_time = time.time()
        self._lock = threading.Lock()

    def register_task(self, name: str, contract_address: Optional[str] = None) -> str:
        """Register a new background task."""
        task_id = f"{name}_{id(asyncio.current_task())}"
        with self._lock:
            self.tasks[task_id] = TaskInfo(
                name=name,
                contract_address=contract_address,
                start_time=time.time(),
                last_heartbeat=time.time(),
            )
        logger.info(
            f"[MONITOR] Task registered: {name} (ID: {task_id}, contract: {contract_address})"
        )
        return task_id

    def unregister_task(self, task_id: str):
        """Unregister a completed task."""
        with self._lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                duration = time.time() - task.start_time
                logger.info(
                    f"[MONITOR] Task completed: {task.name} "
                    f"(duration: {duration:.2f}s, iterations: {task.iteration_count}, errors: {task.errors_count})"
                )
                del self.tasks[task_id]

    def heartbeat(
        self, task_id: str, iteration: Optional[int] = None, message: str = ""
    ):
        """Record a heartbeat for a task."""
        with self._lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                task.last_heartbeat = time.time()
                if iteration is not None:
                    task.iteration_count = iteration

                uptime = time.time() - task.start_time
                logger.debug(
                    f"[HEARTBEAT] {task.name} alive "
                    f"(iteration: {task.iteration_count}, uptime: {uptime:.1f}s, contract: {task.contract_address})"
                    + (f" - {message}" if message else "")
                )

    def record_error(self, task_id: str, error: str):
        """Record an error for a task."""
        with self._lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                task.errors_count += 1
                task.last_error = error
                logger.warning(
                    f"[MONITOR] Task error in {task.name}: {error} "
                    f"(total errors: {task.errors_count})"
                )

    def track_session(self, session_id: int, action: str = "open"):
        """Track database session lifecycle."""
        with self._lock:
            if action == "open":
                self.active_sessions.add(session_id)
                logger.debug(
                    f"[DB_SESSION] Session opened: {session_id} (active: {len(self.active_sessions)})"
                )
            elif action == "close":
                self.active_sessions.discard(session_id)
                logger.debug(
                    f"[DB_SESSION] Session closed: {session_id} (active: {len(self.active_sessions)})"
                )

    def track_processing(self, contract_address: str, tx_hash: Optional[str] = None):
        """Track transaction processing for a contract."""
        with self._lock:
            if tx_hash:
                self.processing_transactions[contract_address] = tx_hash
                logger.info(
                    f"[PROCESSING] Started processing tx {tx_hash} for contract {contract_address}"
                )
            else:
                if contract_address in self.processing_transactions:
                    old_tx = self.processing_transactions[contract_address]
                    del self.processing_transactions[contract_address]
                    logger.info(
                        f"[PROCESSING] Completed processing tx {old_tx} for contract {contract_address}"
                    )

    def get_status(self) -> Dict[str, Any]:
        """Get current monitoring status."""
        with self._lock:
            now = time.time()
            uptime = now - self.start_time

            # Check for stale tasks (no heartbeat in last 60 seconds)
            stale_tasks = []
            for task_id, task in self.tasks.items():
                if now - task.last_heartbeat > 60:
                    stale_tasks.append(
                        {
                            "id": task_id,
                            "name": task.name,
                            "contract": task.contract_address,
                            "last_seen": now - task.last_heartbeat,
                        }
                    )

            # Get system resources
            process = psutil.Process()
            memory_info = process.memory_info()

            return {
                "uptime_seconds": uptime,
                "active_tasks": len(self.tasks),
                "active_sessions": len(self.active_sessions),
                "processing_transactions": len(self.processing_transactions),
                "stale_tasks": stale_tasks,
                "memory_usage_mb": memory_info.rss / 1024 / 1024,
                "cpu_percent": process.cpu_percent(),
                "tasks": {
                    task_id: {
                        "name": task.name,
                        "contract": task.contract_address,
                        "uptime": now - task.start_time,
                        "iterations": task.iteration_count,
                        "errors": task.errors_count,
                        "last_error": task.last_error,
                    }
                    for task_id, task in self.tasks.items()
                },
                "processing": dict(self.processing_transactions),
            }

    def log_status_summary(self):
        """Log a summary of current status."""
        status = self.get_status()
        logger.info(
            f"[MONITOR_SUMMARY] Tasks: {status['active_tasks']}, "
            f"Sessions: {status['active_sessions']}, "
            f"Processing: {status['processing_transactions']}, "
            f"Memory: {status['memory_usage_mb']:.1f}MB, "
            f"CPU: {status['cpu_percent']:.1f}%"
        )

        if status["stale_tasks"]:
            logger.warning(
                f"[MONITOR_WARNING] {len(status['stale_tasks'])} stale tasks detected: "
                f"{[t['name'] for t in status['stale_tasks']]}"
            )


# Global monitor instance
_monitor = ConsensusMonitor()


def get_monitor() -> ConsensusMonitor:
    """Get the global monitor instance."""
    return _monitor


@asynccontextmanager
async def monitored_task(name: str, contract_address: Optional[str] = None):
    """Context manager for monitoring a task."""
    monitor = get_monitor()
    task_id = monitor.register_task(name, contract_address)
    try:
        yield task_id
    except Exception as e:
        monitor.record_error(task_id, str(e))
        raise
    finally:
        monitor.unregister_task(task_id)


@contextmanager
def monitored_session(session):
    """Context manager for monitoring a database session."""
    monitor = get_monitor()
    session_id = id(session)
    monitor.track_session(session_id, "open")
    try:
        yield session
    finally:
        monitor.track_session(session_id, "close")


class OperationTimer:
    """Context manager for timing operations with automatic logging."""

    def __init__(
        self,
        operation_name: str,
        warn_threshold: float = 30.0,
        context: Dict[str, Any] = None,
    ):
        self.operation_name = operation_name
        self.warn_threshold = warn_threshold
        self.context = context or {}
        self.start_time = None
        self.end_time = None

    def __enter__(self):
        self.start_time = time.time()
        logger.debug(f"[TIMING] Starting {self.operation_name}", **self.context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.time()
        duration = self.end_time - self.start_time

        log_data = {"duration_seconds": duration, **self.context}

        if exc_type:
            logger.error(
                f"[TIMING] {self.operation_name} failed after {duration:.2f}s: {exc_val}",
                **log_data,
            )
        elif duration > self.warn_threshold:
            logger.warning(
                f"[TIMING] {self.operation_name} took {duration:.2f}s (threshold: {self.warn_threshold}s)",
                **log_data,
            )
        else:
            logger.debug(
                f"[TIMING] {self.operation_name} completed in {duration:.2f}s",
                **log_data,
            )

    @property
    def duration(self) -> Optional[float]:
        """Get the duration of the operation."""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None


async def periodic_status_logger(interval: int = 30):
    """
    Periodically log status summary.
    Run this as a background task to get regular health updates.
    """
    monitor = get_monitor()
    while True:
        await asyncio.sleep(interval)
        monitor.log_status_summary()
