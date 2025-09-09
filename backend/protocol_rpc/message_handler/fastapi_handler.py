import os
import json
import copy
import asyncio
from functools import wraps
import traceback
from typing import Optional, Any

from loguru import logger
import sys

from backend.protocol_rpc.message_handler.types import LogEvent
from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.protocol_rpc.message_handler.types import EventScope, EventType, LogEvent

MAX_LOG_MESSAGE_LENGTH = 3000


class MessageHandler:
    """FastAPI-compatible MessageHandler using WebSocket ConnectionManager."""

    def __init__(self, connection_manager, config: GlobalConfiguration):
        self.connection_manager = connection_manager
        self.config = config
        self.client_session_id = None
        # Store the emit function for async operations
        self._emit_task_queue = []

    def with_client_session(self, client_session_id: str):
        new_msg_handler = MessageHandler(self.connection_manager, self.config)
        new_msg_handler.client_session_id = client_session_id
        return new_msg_handler

    def log_endpoint_info(self, func):
        """Decorator for logging endpoint information."""

        @wraps(func)
        def wrapper(*args, **kwargs):
            # Log endpoint call
            logger.info(f"Endpoint called: {func.__name__}")
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                logger.error(f"Endpoint error in {func.__name__}: {e}")
                raise

        return wrapper

    def _socket_emit(self, log_event: LogEvent):
        """Emit a log event via WebSocket."""
        try:
            if log_event.transaction_hash:
                # Schedule async emit to room
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        self.connection_manager.emit_to_room(
                            log_event.transaction_hash,
                            log_event.name,
                            log_event.to_dict(),
                        )
                    )
            elif log_event.scope == EventScope.RPC:
                # Broadcast to all connections
                message = json.dumps(
                    {"event": log_event.name, "data": log_event.to_dict()}
                )
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.connection_manager.broadcast(message))
        except RuntimeError:
            # No event loop running, skip WebSocket emission
            pass

    def _log_event(self, log_event: LogEvent):
        """Log an event to the appropriate channels."""
        # Console logging
        if log_event.type == EventType.ERROR:
            logger.error(log_event.message)
        elif log_event.type == EventType.WARNING:
            logger.warning(log_event.message)
        else:
            logger.info(log_event.message)

        # WebSocket emission
        self._socket_emit(log_event)

    def log(self, message: str, level: str = "info", **kwargs):
        """Generic logging method."""
        log_event = LogEvent(
            name="log",
            type=EventType.INFO if level == "info" else EventType.ERROR,
            message=message,
            scope=EventScope.RPC,
            **kwargs,
        )
        self._log_event(log_event)

    def error(self, message: str, **kwargs):
        """Log an error message."""
        log_event = LogEvent(
            name="error",
            type=EventType.ERROR,
            message=message,
            scope=EventScope.RPC,
            **kwargs,
        )
        self._log_event(log_event)

    def warning(self, message: str, **kwargs):
        """Log a warning message."""
        log_event = LogEvent(
            name="warning",
            type=EventType.WARNING,
            message=message,
            scope=EventScope.RPC,
            **kwargs,
        )
        self._log_event(log_event)

    def info(self, message: str, **kwargs):
        """Log an info message."""
        log_event = LogEvent(
            name="info",
            type=EventType.INFO,
            message=message,
            scope=EventScope.RPC,
            **kwargs,
        )
        self._log_event(log_event)

    # Transaction-specific logging methods
    def send_transaction_status_update(
        self, transaction_hash: str, status: str, **kwargs
    ):
        """Send transaction status update via WebSocket."""
        log_event = LogEvent(
            name="transaction_status_updated",  # Match frontend expectation
            type=EventType.INFO,
            message=f"Transaction {transaction_hash} status: {status}",
            transaction_hash=transaction_hash,
            data={"hash": transaction_hash, "status": status, **kwargs},
            scope=EventScope.TRANSACTION,
        )
        self._socket_emit(log_event)

    def send_transaction_event(self, transaction_hash: str, event_name: str, data: Any):
        """Send a custom transaction event."""
        log_event = LogEvent(
            name=event_name,
            type=EventType.INFO,
            message=f"Transaction event: {event_name}",
            transaction_hash=transaction_hash,
            data=data,
            scope=EventScope.TRANSACTION,
        )
        self._socket_emit(log_event)

    def send_message(self, log_event: LogEvent):
        """Send a message via WebSocket. Compatibility method for Flask code."""
        self._socket_emit(log_event)


def setup_loguru_config():
    """Set up unified logging configuration using Loguru."""
    # Remove default handler
    logger.remove()

    # Add custom handler with formatting
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    # Console handler
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True,
    )

    # File handler (optional)
    if os.environ.get("LOG_TO_FILE"):
        logger.add(
            "logs/app.log",
            rotation="10 MB",
            retention="7 days",
            level=log_level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        )

    return logger
