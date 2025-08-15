import os
import json
import copy
from functools import wraps
from logging.config import dictConfig
import traceback
import base64

from flask import request
from flask_jsonrpc.exceptions import JSONRPCError
from loguru import logger
import sys

from flask_socketio import SocketIO

from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.protocol_rpc.message_handler.types import EventScope, EventType, LogEvent
from backend.node.genvm.origin import calldata

MAX_LOG_MESSAGE_LENGTH = 3000


# TODO: this should probably live in another module
def get_client_session_id() -> str:
    try:
        return request.headers.get("x-session-id")
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
                room=log_event.transaction_hash,
            )
        else:
            client_session_id = (
                log_event.client_session_id
                or self.client_session_id
                or get_client_session_id()
            )
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

    def _decode_value(self, value, key="", parent_key=""):
        """Main entry point for decoding any type of value.
        Provides a unified interface to decode complex nested data structures by recursively
        processing different data types and delegating to specialized decoders based on
        the value type and contextual key information.
        """
        if isinstance(value, dict):
            return {k: self._decode_value(v, k, key) for k, v in value.items()}

        elif isinstance(value, list):
            return [self._decode_value(item, key, parent_key) for item in value]

        elif isinstance(value, str):
            if not value:
                return value

            decoded_value = self._decode_by_key(value, key, parent_key)

            # If it's still a string, check if it's JSON that might contain data fields
            if isinstance(decoded_value, str) and decoded_value != value:
                return decoded_value
            elif isinstance(decoded_value, str):
                return self._try_decode_json_with_data_fields(decoded_value)
            else:
                return decoded_value

        elif isinstance(value, (bytes, memoryview)):
            decoded_bytes = bytes(value) if isinstance(value, memoryview) else value
            return self._decode_bytes_by_key(decoded_bytes, key, parent_key)

        else:
            return value

    def _decode_by_key(self, value, key, parent_key=""):
        """Converts base64-encoded strings to raw bytes.
        Many values in the system are base64-encoded and require different decoding
        strategies based on their field context. Attempts base64 decoding and delegates
        to byte-level decoding logic, returning the original value if base64 decoding fails.
        """
        try:
            decoded_bytes = base64.b64decode(value, validate=True)
            return self._decode_bytes_by_key(decoded_bytes, key, parent_key)
        except ValueError:
            return value

    def _decode_bytes_by_key(self, decoded_bytes, key, parent_key=""):
        """Core decoding logic that transforms raw bytes based on field name context.
        Different fields require different decoding strategies.
        Contract code needs UTF-8 decoding, results should be hex-encoded, args need storage slot decoding,
        calldata requires GenVM decoding, and storage slots need specialized handling. Uses
        field names and parent context to determine the most appropriate decoding method.
        """
        if key in ["contract_code", "code"]:
            try:
                return decoded_bytes.decode("utf-8")
            except UnicodeDecodeError:
                if len(decoded_bytes) >= 2 and decoded_bytes[:2] == b"PK":
                    # Keep ZIP files as base64
                    return base64.b64encode(decoded_bytes).decode("ascii")
                return decoded_bytes.hex()

        if key == "result":
            return decoded_bytes.hex()

        if key == "args":
            return self._decode_storage_slot(decoded_bytes)

        if key == "calldata":
            try:
                result = calldata.decode(decoded_bytes)
                # Convert non-serializable objects to strings for JSON compatibility
                return self._convert_non_serializable_objects(result)
            except Exception:
                return self._decode_storage_slot(decoded_bytes)

        # Contract state values - only decode GenVM contract code, leave others as base64 because we do not know the type
        if parent_key in [
            "contract_state",
            "accepted",
            "finalized",
        ] or self._is_storage_slot_key(key):
            try:
                result = calldata.decode(decoded_bytes)
                if (
                    isinstance(result, str)
                    and len(result) > 10
                    and any(
                        keyword in result
                        for keyword in ["class", "def", "import", "from", "#"]
                    )
                ):
                    return result
            except Exception:
                # For contract code with GenVM prefixes, try manual UTF-8 decode after 4-byte header
                # Only do this if we detect actual GenVM prefixes (0x00 0x02, 0x01 0x02, 0xf4, 0xf5, etc.)
                if len(decoded_bytes) >= 4:
                    first_bytes = decoded_bytes[:4]
                    # Check for known GenVM prefixes
                    if (
                        first_bytes[:2] == b"\x00\x02"
                        or first_bytes[:2] == b"\x01\x02"
                        or first_bytes[0] in (0xF4, 0xF5)
                    ):
                        try:
                            result = decoded_bytes[4:].decode("utf-8")
                            if len(result) > 10 and any(
                                keyword in result
                                for keyword in ["class", "def", "import", "from", "#"]
                            ):
                                return result
                        except UnicodeDecodeError:
                            pass

            return base64.b64encode(decoded_bytes).decode("ascii")

        return decoded_bytes.hex()

    def _is_storage_slot_key(self, key):
        """Identifies storage slot keys to apply appropriate decoding strategies.
        Storage slot keys are 32-byte hashes encoded as base64 and need different
        handling than regular field names. Validates key format by checking length
        constraints and attempting base64 decoding to confirm it represents a
        32-byte hash value.
        """
        if not key or len(key) < 40:
            return False

        if key.endswith("=") and len(key) in [44, 43]:
            try:
                decoded = base64.b64decode(key, validate=True)
                return len(decoded) == 32
            except ValueError:
                return False

        return False

    def _decode_storage_slot(self, decoded_bytes):
        """Specialized decoder for storage slot data in args and calldata fields.
        Storage slots can contain GenVM-encoded data, plain text, or binary data.
        Attempts multiple decoding strategies in order of likelihood: GenVM decoding
        for structured data, UTF-8 text decoding for readable content, and fallback
        encoding for binary data that cannot be meaningfully decoded.
        """
        if not decoded_bytes:
            return ""

        genvm_result = self._try_decode_as_genvm(decoded_bytes)
        if genvm_result is not None:
            return genvm_result

        text_result = self._try_decode_as_text(decoded_bytes)
        if text_result is not None:
            return text_result

        return self._fallback_encoding(decoded_bytes)

    def _try_decode_as_genvm(self, decoded_bytes):
        """Attempts to decode GenVM-encoded data using format-specific prefixes.
        GenVM uses specific encoding formats that require special handling for
        smart contract data. Checks for known GenVM prefixes and uses the calldata
        decoder, with fallback logic to skip headers and decode as UTF-8 for
        contract code when standard GenVM decoding fails.
        """
        if len(decoded_bytes) < 4:
            return None

        if decoded_bytes[:2] == b"\x00\x02" or decoded_bytes[0] in (0xF4, 0xF5):
            try:
                return calldata.decode(decoded_bytes)
            except Exception:
                if decoded_bytes[:2] == b"\x00\x02":
                    try:
                        return decoded_bytes[4:].decode("utf-8")
                    except UnicodeDecodeError:
                        pass

        return None

    def _try_decode_as_text(self, decoded_bytes):
        """Attempts UTF-8 text decoding with readability validation.
        Some data is plain text that has been base64 encoded during transmission.
        Performs UTF-8 decoding and validates that the resulting text contains only
        printable characters to avoid displaying binary garbage as text.
        """
        try:
            text = decoded_bytes.decode("utf-8")
            return text if self._is_readable_text(text) else None
        except UnicodeDecodeError:
            return None

    def _fallback_encoding(self, decoded_bytes):
        """Final fallback encoding when other decoding methods fail.
        Ensures meaningful output is always returned even for unrecognized binary data.
        Uses hex encoding for short data (8 bytes or less) for readability, and
        base64 encoding for longer data to maintain compactness while preserving
        the original information.
        """
        if len(decoded_bytes) <= 8:
            return decoded_bytes.hex()
        else:
            return base64.b64encode(decoded_bytes).decode("ascii")

    def _is_readable_text(self, text):
        """Validates that decoded text contains only human-readable characters.
        Prevents displaying binary garbage as text in log output by ensuring
        all characters are either printable or acceptable whitespace (newlines,
        carriage returns, tabs). Returns false for empty strings or text
        containing non-printable control characters.
        """
        if not text:
            return False

        for char in text:
            if not (char.isprintable() or char in "\n\r\t"):
                return False

        return True

    def _try_decode_json_with_data_fields(self, json_string):
        """Parses JSON strings and recursively decodes embedded 'data' fields.
        Some fields contain JSON with base64-encoded 'data' fields that need
        specialized decoding. Attempts JSON parsing and recursively processes
        the structure to decode any 'data' fields, returning the original
        string if JSON parsing fails or the content is not a dictionary.
        """
        try:
            parsed = json.loads(json_string)

            if isinstance(parsed, dict):
                return self._decode_json_data_fields(parsed)
            else:
                return json_string

        except (json.JSONDecodeError, TypeError):
            return json_string

    def _decode_json_data_fields(self, obj):
        """Recursively processes JSON structures to decode GenVM-encoded 'data' fields.
        'data' fields in JSON are typically GenVM-encoded and require special handling
        for readability. Traverses the JSON structure recursively, identifies 'data'
        fields, attempts base64 decoding followed by GenVM decoding, and handles
        partial decode errors by extracting meaningful content from error messages.
        """
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                if k == "data" and isinstance(v, str):
                    try:
                        decoded_bytes = base64.b64decode(v, validate=True)
                        genvm_result = calldata.decode(decoded_bytes)
                        result[k] = genvm_result
                    except Exception as e:
                        # Handle partial decode errors where content is in error message
                        error_msg = str(e)
                        if "decoded" in error_msg and "unparsed end" in error_msg:
                            try:
                                start = error_msg.find("(decoded ") + 9
                                end = error_msg.rfind(")")
                                if start > 8 and end > start:
                                    result[k] = error_msg[start:end]
                                else:
                                    result[k] = v
                            except Exception:
                                result[k] = v
                        else:
                            result[k] = v
                else:
                    result[k] = self._decode_json_data_fields(v)
            return result
        elif isinstance(obj, list):
            return [self._decode_json_data_fields(item) for item in obj]
        else:
            return obj

    def _convert_non_serializable_objects(self, obj):
        """Converts objects that cannot be JSON serialized into string representations.
        The logging system requires all data to be JSON serializable for transmission
        and storage. Recursively processes data structures to convert memoryview objects
        to hex strings, Address objects to their string representation, and handles
        non-ASCII strings that might cause JSON serialization issues.
        """
        if isinstance(obj, memoryview):
            return f"b#{bytes(obj).hex()}"
        elif hasattr(obj, "__class__") and obj.__class__.__name__ == "Address":
            # Handle Address objects (they use __slots__ so no __dict__)
            return str(obj)  # Uses __repr__ which returns "addr#..." format
        elif isinstance(obj, dict):
            return {
                k: self._convert_non_serializable_objects(v) for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [self._convert_non_serializable_objects(item) for item in obj]
        elif isinstance(obj, str):
            # Check if string contains non-ASCII characters that might cause JSON issues
            try:
                obj.encode("ascii")
                return obj  # ASCII string is fine
            except UnicodeEncodeError:
                # Non-ASCII string, convert to hex if it looks like decoded binary data
                if len(obj) <= 4 and any(ord(c) > 127 for c in obj):
                    return obj.encode("utf-8").hex()
                return obj  # Keep longer text as-is
        else:
            return obj

    def _apply_log_level_truncation(self, data, max_length=200):
        """Main orchestrator for decoding and truncating log data for optimal readability.
        Transforms raw binary/encoded data into human-readable formats while managing
        log verbosity. Applies comprehensive decoding to make data meaningful, ensures
        all objects are JSON serializable, and optionally truncates verbose output
        based on log level configuration to balance detail with readability.
        """
        decoded_data = self._decode_value(data)
        decoded_data = self._convert_non_serializable_objects(decoded_data)

        # Only truncate if not in DEBUG mode
        should_truncate = os.environ.get("LOG_LEVEL", "INFO").upper() != "DEBUG"

        if not should_truncate or not isinstance(decoded_data, dict):
            return decoded_data

        truncated_data = copy.deepcopy(decoded_data)
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


def log_endpoint_info_wrapper(msg_handler: MessageHandler, config: GlobalConfiguration):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            shouldPrintInfoLogs = (
                func.__name__ not in config.get_disabled_info_logs_endpoints()
            )

            if shouldPrintInfoLogs:
                msg_handler.send_message(
                    LogEvent(
                        "endpoint_call",
                        EventType.INFO,
                        EventScope.RPC,
                        "Endpoint called: " + func.__name__,
                        {"endpoint_name": func.__name__, "args": args},
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
