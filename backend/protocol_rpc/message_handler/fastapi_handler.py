import asyncio
import copy
import json
import os
import sys
import traceback
from functools import wraps
from typing import Any

from loguru import logger

from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.protocol_rpc.message_handler.types import (
    EventScope,
    EventType,
    LogEvent,
)
from backend.protocol_rpc.broadcast import Broadcast
from backend.protocol_rpc.websocket import GLOBAL_CHANNEL

MAX_LOG_MESSAGE_LENGTH = 3000


class MessageHandler:
    """FastAPI-compatible MessageHandler backed by Starlette Broadcast."""

    def __init__(self, broadcast: Broadcast, config: GlobalConfiguration):
        self.broadcast = broadcast
        self.config = config
        self.client_session_id = None

    def with_client_session(self, client_session_id: str):
        new_msg_handler = MessageHandler(self.broadcast, self.config)
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

    def _publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Queue a broadcast publish for the given channel."""

        if self.broadcast is None:
            return

        message = json.dumps(payload)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        if not loop.is_running():
            return

        loop.create_task(self.broadcast.publish(channel=channel, message=message))

    def _socket_emit(self, log_event: LogEvent) -> None:
        """Emit a log event via broadcast channels."""

        payload = {"event": log_event.name, "data": log_event.to_dict()}

        if log_event.transaction_hash:
            self._publish(log_event.transaction_hash, payload)
        else:
            self._publish(GLOBAL_CHANNEL, payload)

    def _log_event(self, log_event: LogEvent):
        """Log an event to the appropriate channels."""
        # Console logging with optional data payload
        message = log_event.message
        gray = "\033[38;5;245m"
        reset = "\033[0m"

        if getattr(log_event, "data", None):
            try:
                data_to_log = self._apply_log_level_truncation(log_event.data)
                data_str = json.dumps(data_to_log, default=lambda o: o.__dict__)
                message = f"{message} {gray}{data_str}{reset}"
            except TypeError as e:
                message = f"{message} {gray}{str(log_event.data)} (serialization error: {e}){reset}"

        if log_event.type == EventType.ERROR:
            logger.error(message)
        elif log_event.type == EventType.WARNING:
            logger.warning(message)
        else:
            logger.info(message)

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

    def send_message(self, log_event: LogEvent, log_to_terminal: bool = True):
        """Send a message via WebSocket and optionally log to terminal."""
        if log_to_terminal:
            self._log_event(log_event)
        else:
            # Just emit via WebSocket without terminal logging
            self._socket_emit(log_event)

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

    def _apply_log_level_truncation(self, data, max_length=100):
        """Apply LOG_LEVEL-based truncation to log data for better readability."""
        should_truncate = os.environ.get("LOG_LEVEL", "INFO").upper() != "DEBUG"

        if not should_truncate or not isinstance(data, dict):
            return data

        truncated_data = copy.deepcopy(data)
        self._truncate_dict(truncated_data, max_length)

        return truncated_data

    def _truncate_dict(self, data_dict, max_length):
        """Recursively truncate dictionary values based on key patterns."""
        if not isinstance(data_dict, dict):
            return

        for key in ["calldata", "contract_code", "result"]:
            if (
                key in data_dict
                and isinstance(data_dict[key], str)
                and len(data_dict[key]) > max_length
            ):
                data_dict[key] = (
                    f"{data_dict[key][:max_length]}... ({len(data_dict[key])} chars)"
                )

        if "contract_state" in data_dict and data_dict["contract_state"]:
            value = data_dict["contract_state"]
            if len(str(value)) > max_length:
                if isinstance(value, dict):
                    data_dict["contract_state"] = f"<{len(value)} entries, truncated>"
                else:
                    data_dict["contract_state"] = (
                        f"<{len(str(value))} chars, truncated>"
                    )

        if "state" in data_dict:
            data_dict["state"] = "<truncated>"

        if "code" in data_dict and isinstance(data_dict["code"], str):
            data_dict["code"] = f"<{len(data_dict['code'])} chars>"

        for key, value in data_dict.items():
            if isinstance(value, dict):
                self._truncate_dict(value, max_length)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._truncate_dict(item, max_length)


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
