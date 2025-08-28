import os
import json
import copy
from functools import wraps
from logging.config import dictConfig
import traceback

from flask import request
from eth_utils.address import to_checksum_address
from eth_account import Account
from flask_jsonrpc.exceptions import JSONRPCError
from loguru import logger
import sys

from backend.protocol_rpc.message_handler.types import LogEvent
from flask_socketio import SocketIO

from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.protocol_rpc.message_handler.types import EventScope, EventType, LogEvent

MAX_LOG_MESSAGE_LENGTH = 3000


# TODO: this should probably live in another module
def get_client_session_id() -> str:
    try:
        return request.headers.get("x-session-id", "")
    except RuntimeError:  # when this is called outside of a request
        return ""


class MessageHandler:
    def __init__(self, socketio: SocketIO, config: GlobalConfiguration):
        self.socketio = socketio
        self.config = config
        self.client_session_id = None
        setup_logging_config()

    def with_client_session(self, client_session_id: str):
        new_msg_handler = MessageHandler(self.socketio, self.config)
        new_msg_handler.client_session_id = client_session_id
        return new_msg_handler

    def log_endpoint_info(self, func):
        return log_endpoint_info_wrapper(self, self.config)(func)

    def _socket_emit(self, log_event: LogEvent):
        if log_event.transaction_hash:
            self.socketio.emit(
                log_event.name,
                log_event.to_dict(),
                to=log_event.transaction_hash,
            )
        elif log_event.account_address:
            self.socketio.emit(
                log_event.name,
                log_event.to_dict(),
                to=log_event.account_address,
            )
        else:
            client_session_id = (
                log_event.client_session_id
                or self.client_session_id
                or get_client_session_id()
            )

            if client_session_id:
                self.socketio.emit(
                    log_event.name,
                    log_event.to_dict(),
                    to=client_session_id,
                )

    def _log_message(self, log_event: LogEvent):
        logging_status = log_event.type.value

        if not hasattr(logger, logging_status):
            logging_status = "info"

        log_method = getattr(logger, logging_status)

        message = (
            (log_event.message[:MAX_LOG_MESSAGE_LENGTH] + "...")
            if log_event.message is not None
            and len(log_event.message) > MAX_LOG_MESSAGE_LENGTH
            else log_event.message
        )

        log_message = f"[{log_event.scope.value}] {message}"
        gray = "\033[38;5;245m"
        reset = "\033[0m"

        if log_event.data:
            try:
                data_to_log = self._apply_log_level_truncation(log_event.data)
                data_str = json.dumps(data_to_log, default=lambda o: o.__dict__)
                log_message += f" {gray}{data_str}{reset}"
            except TypeError as e:
                log_message += (
                    f" {gray}{str(log_event.data)} (serialization error: {e}){reset}"
                )

        log_method(log_message)

    def _apply_log_level_truncation(self, data, max_length=100):
        """Apply LOG_LEVEL-based truncation to log data for better readability."""
        # Only truncate if not in DEBUG mode
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

        # String fields that should be truncated with length info
        for key in ["calldata", "contract_code", "result"]:
            if (
                key in data_dict
                and isinstance(data_dict[key], str)
                and len(data_dict[key]) > max_length
            ):
                data_dict[key] = (
                    f"{data_dict[key][:max_length]}... ({len(data_dict[key])} chars)"
                )

        # Contract state - show entry count when truncated
        if "contract_state" in data_dict and data_dict["contract_state"]:
            value = data_dict["contract_state"]
            if len(str(value)) > max_length:
                if isinstance(value, dict):
                    data_dict["contract_state"] = f"<{len(value)} entries, truncated>"
                else:
                    data_dict["contract_state"] = (
                        f"<{len(str(value))} chars, truncated>"
                    )

        # Contract state field - simple truncation message
        if "state" in data_dict:
            data_dict["state"] = "<truncated>"

        # Contract code field - show character count
        if "code" in data_dict and isinstance(data_dict["code"], str):
            data_dict["code"] = f"<{len(data_dict['code'])} chars>"

        # Recursively process nested dictionaries and lists
        for key, value in data_dict.items():
            if isinstance(value, dict):
                self._truncate_dict(value, max_length)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._truncate_dict(item, max_length)

    def send_message(self, log_event: LogEvent, log_to_terminal: bool = True):
        if log_to_terminal:
            self._log_message(log_event)
        self._socket_emit(log_event)


def _extract_account_address_from_endpoint(func_name: str, args: tuple) -> str | None:
    """Extract account address from endpoint function name and arguments."""
    try:
        if (
            func_name in ["eth_getBalance", "eth_getTransactionCount"]
            and len(args) >= 1
        ):
            return args[0]
        elif (
            func_name
            in ["eth_sendTransaction", "eth_call", "gen_call", "eth_estimateGas"]
            and len(args) >= 1
        ):
            if isinstance(args[0], dict) and "from" in args[0]:
                return args[0]["from"]
        elif func_name == "eth_sendRawTransaction" and len(args) >= 1:
            try:
                sender = Account.recover_transaction(args[0])
                return to_checksum_address(sender)
            except Exception:
                return None
        return None
    except Exception:
        return None


def log_endpoint_info_wrapper(msg_handler: MessageHandler, config: GlobalConfiguration):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            shouldPrintInfoLogs = (
                func.__name__ not in config.get_disabled_info_logs_endpoints()
            )

            account_address = _extract_account_address_from_endpoint(
                func.__name__, args
            )

            if shouldPrintInfoLogs:
                msg_handler.send_message(
                    LogEvent(
                        "endpoint_call",
                        EventType.INFO,
                        EventScope.RPC,
                        "Endpoint called: " + func.__name__,
                        {"endpoint_name": func.__name__, "args": args},
                        account_address=account_address,
                    )
                )
            try:
                result = func(*args, **kwargs)
                if hasattr(result, "__await__"):
                    result = await result
                if shouldPrintInfoLogs:
                    msg_handler.send_message(
                        LogEvent(
                            "endpoint_success",
                            EventType.SUCCESS,
                            EventScope.RPC,
                            "Endpoint responded: " + func.__name__,
                            {
                                "endpoint_name": func.__name__,
                                "result": result,
                            },
                            account_address=account_address,
                        )
                    )
                return result
            except Exception as e:
                as_jsonrpc = None
                if isinstance(e, JSONRPCError):
                    as_jsonrpc = e.jsonrpc_format
                msg_handler.send_message(
                    LogEvent(
                        "endpoint_error",
                        EventType.ERROR,
                        EventScope.RPC,
                        f"Error executing endpoint {func.__name__ }: {str(e)}",
                        {
                            "endpoint_name": func.__name__,
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                            "jsonrpc_error": as_jsonrpc,
                        },
                        account_address=account_address,
                    )
                )
                raise e

        return wrapper

    return decorator


def setup_logging_config():
    logging_env = os.environ["LOGCONFIG"]
    file_path = (
        f"backend/protocol_rpc/message_handler/config/logging.{logging_env}.json"
    )
    with open(file_path, "r") as file:
        logging_config = json.load(file)
        dictConfig(logging_config)

    logger.remove()
    logger.add(
        sys.stdout,
        colorize=True,
        format="<level>{level: <8}</level> | {message}",
    )
