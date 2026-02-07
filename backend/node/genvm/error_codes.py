# backend/node/genvm/error_codes.py

"""
Standardized error codes for GenVM/LLM operations.

This module provides structured error codes that can be extracted from GenVM execution
results and Lua error structures, allowing users to understand why operations failed
(rate limiting, no provider, timeouts, etc.).
"""

import re
from enum import StrEnum
from typing import Optional

__all__ = (
    "GenVMErrorCode",
    "GenVMInternalError",
    "LLM_ERROR_CODES",
    "LUA_CAUSE_TO_CODE",
    "RATE_LIMIT_STATUSES",
    "extract_error_code",
    "parse_module_error_string",
)


class GenVMInternalError(Exception):
    """
    Raised when GenVM returns INTERNAL_ERROR (result_kind = 3).

    This indicates a critical failure that should trigger worker recovery
    when it occurs during leader execution:
    - Stop processing the current transaction
    - Release the transaction for another worker to pick up
    - Increment failure counter for health check
    - Allow K8s to restart the pod when unhealthy

    When this error occurs during validator execution, the consensus can
    continue with the remaining validators.
    """

    def __init__(
        self,
        message: str,
        error_code: str | None,
        causes: list[str],
        is_fatal: bool,
        is_leader: bool | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.causes = causes
        self.is_fatal = is_fatal
        self.is_leader = is_leader

    def __repr__(self) -> str:
        return (
            f"GenVMInternalError(message={self.args[0]!r}, "
            f"error_code={self.error_code!r}, causes={self.causes!r}, "
            f"is_fatal={self.is_fatal!r}, is_leader={self.is_leader!r})"
        )


class GenVMErrorCode(StrEnum):
    """Standardized error codes for GenVM execution failures."""

    # LLM-related errors
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_NO_PROVIDER = "LLM_NO_PROVIDER"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    LLM_INVALID_API_KEY = "LLM_INVALID_API_KEY"
    LLM_TIMEOUT = "LLM_TIMEOUT"

    # Web request errors
    WEB_REQUEST_FAILED = "WEB_REQUEST_FAILED"
    WEB_TLD_FORBIDDEN = "WEB_TLD_FORBIDDEN"

    # Execution errors
    GENVM_TIMEOUT = "GENVM_TIMEOUT"
    CONTRACT_ERROR = "CONTRACT_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Mapping from Lua error causes to standardized error codes
LUA_CAUSE_TO_CODE: dict[str, GenVMErrorCode] = {
    "NO_PROVIDER_FOR_PROMPT": GenVMErrorCode.LLM_NO_PROVIDER,
    "STATUS_NOT_OK": GenVMErrorCode.LLM_PROVIDER_ERROR,
    "WEBPAGE_LOAD_FAILED": GenVMErrorCode.WEB_REQUEST_FAILED,
    "TLD_FORBIDDEN": GenVMErrorCode.WEB_TLD_FORBIDDEN,
}

# HTTP status codes that indicate rate limiting
RATE_LIMIT_STATUSES: set[int] = {429, 503, 529}

# LLM error codes that indicate infrastructure/provider failures (not contract bugs).
# These should trigger Vote.TIMEOUT for validators and GenVMInternalError for leaders.
LLM_ERROR_CODES: frozenset[str] = frozenset({
    GenVMErrorCode.LLM_RATE_LIMITED,
    GenVMErrorCode.LLM_NO_PROVIDER,
    GenVMErrorCode.LLM_PROVIDER_ERROR,
    GenVMErrorCode.LLM_INVALID_API_KEY,
    GenVMErrorCode.LLM_TIMEOUT,
})

# Regex pattern for extracting fatal flag from ModuleError
_FATAL_PATTERN = re.compile(r"fatal:\s*(true|false)")

# Pattern to find the first cause in the causes array
# Handles escaped quotes by matching a simple identifier (letters, underscores, numbers)
_FIRST_CAUSE_PATTERN = re.compile(r'causes:\s*\["([A-Z_][A-Z0-9_]*)"')

# Patterns for fallback detection when structured ModuleError format is not available
_LLM_STACK_PATTERN = re.compile(
    r"(lib-llm\.exec_prompt|llm\.lua|exec_prompt_in_provider)", re.IGNORECASE
)
_WEB_STACK_PATTERN = re.compile(r"(web\.lua|webpage_load|fetch_url)", re.IGNORECASE)


def parse_module_error_string(error_str: str) -> tuple[str | None, list[str], bool]:
    """
    Parse ModuleError string to extract causes, fatal flag, and error code.

    The GenVM returns errors in a Rust-like format:
        ModuleError { causes: ["STATUS_NOT_OK", "stack traceback:..."], fatal: true, ctx: {...} }

    The first cause is typically the error code (e.g., "STATUS_NOT_OK", "NO_PROVIDER_FOR_PROMPT").
    Subsequent causes contain stack traces which we don't need to parse in detail.

    For errors without the structured format, we attempt to detect the error type
    from the Lua stack trace (e.g., LLM or web request errors).

    Args:
        error_str: The error string from GenVM result_data

    Returns:
        Tuple of (error_code, causes, is_fatal):
        - error_code: Mapped GenVMErrorCode or None
        - causes: List of cause strings (just the first/primary cause)
        - is_fatal: Whether the error is fatal
    """
    causes: list[str] = []
    is_fatal = False

    # Extract the first cause (the error code identifier)
    # This is typically something like "STATUS_NOT_OK" or "NO_PROVIDER_FOR_PROMPT"
    first_cause_match = _FIRST_CAUSE_PATTERN.search(error_str)
    if first_cause_match:
        causes = [first_cause_match.group(1)]

    # Extract fatal flag
    fatal_match = _FATAL_PATTERN.search(error_str)
    if fatal_match:
        is_fatal = fatal_match.group(1) == "true"

    # Map first cause to error code
    error_code: str | None = None
    if causes:
        error_code = LUA_CAUSE_TO_CODE.get(causes[0], GenVMErrorCode.INTERNAL_ERROR)
    else:
        # Fallback: detect error type from stack trace patterns
        # This handles errors without the structured ModuleError format
        if _LLM_STACK_PATTERN.search(error_str):
            error_code = GenVMErrorCode.LLM_PROVIDER_ERROR
            causes = ["LLM_EXECUTION_ERROR"]
        elif _WEB_STACK_PATTERN.search(error_str):
            error_code = GenVMErrorCode.WEB_REQUEST_FAILED
            causes = ["WEB_EXECUTION_ERROR"]

    return error_code, causes, is_fatal


def extract_error_code(
    result_data: dict | str | None, stderr: str = ""
) -> Optional[str]:
    """
    Extract a standardized error code from GenVM result_data or stderr.

    Args:
        result_data: The result_data from GenVM execution (dict with causes/ctx or string)
        stderr: The stderr output from GenVM execution

    Returns:
        A GenVMErrorCode string value if an error code can be determined, None otherwise

    Example Lua error structure:
        {
            "message": "error message",
            "causes": ["NO_PROVIDER_FOR_PROMPT"],
            "fatal": true,
            "ctx": {
                "status": 429,
                "body": {"error": {"code": 429, "message": "rate-limited upstream"}},
                "url": "https://openrouter.ai/api/v1/chat/completions"
            }
        }
    """
    if result_data is None:
        return None

    if isinstance(result_data, str):
        # Try to extract error code from string message
        return _extract_from_message(result_data, stderr)

    if not isinstance(result_data, dict):
        return None

    # Check for Lua causes array
    causes = result_data.get("causes", [])
    ctx = result_data.get("ctx", {})

    # First, check for rate limiting by HTTP status
    if isinstance(ctx, dict):
        status = ctx.get("status")
        if isinstance(status, int) and status in RATE_LIMIT_STATUSES:
            return GenVMErrorCode.LLM_RATE_LIMITED

        # Check body for rate limit indicators
        body = ctx.get("body", {})
        if isinstance(body, dict):
            error_info = body.get("error", {})
            if isinstance(error_info, dict):
                error_code = error_info.get("code")
                if isinstance(error_code, int) and error_code in RATE_LIMIT_STATUSES:
                    return GenVMErrorCode.LLM_RATE_LIMITED

    # Map Lua causes to error codes
    if isinstance(causes, list):
        for cause in causes:
            if cause in LUA_CAUSE_TO_CODE:
                # Special case: STATUS_NOT_OK might be rate limiting based on ctx
                if cause == "STATUS_NOT_OK":
                    # Already checked for rate limiting above, so this is a general provider error
                    return GenVMErrorCode.LLM_PROVIDER_ERROR
                return LUA_CAUSE_TO_CODE[cause]

    # Check message for specific patterns
    message = result_data.get("message", "")
    return _extract_from_message(message, stderr)


def _extract_from_message(message: str, stderr: str = "") -> Optional[str]:
    """Extract error code from message string or stderr."""
    combined = f"{message} {stderr}".lower()

    # Check for specific error patterns
    if "rate limit" in combined or "429" in combined:
        return GenVMErrorCode.LLM_RATE_LIMITED

    if "invalid api key" in combined or "authentication" in combined:
        return GenVMErrorCode.LLM_INVALID_API_KEY

    if "timeout" in combined:
        # Distinguish between LLM timeout and general timeout
        if "llm" in combined or "provider" in combined or "openai" in combined:
            return GenVMErrorCode.LLM_TIMEOUT
        return GenVMErrorCode.GENVM_TIMEOUT

    if "no provider" in combined:
        return GenVMErrorCode.LLM_NO_PROVIDER

    return None


def extract_error_code_from_timeout(last_error: Exception | None) -> str:
    """
    Extract an appropriate error code for timeout scenarios.

    Args:
        last_error: The last exception that occurred before timeout

    Returns:
        A GenVMErrorCode string value
    """
    if last_error is None:
        return GenVMErrorCode.GENVM_TIMEOUT

    error_str = str(last_error).lower()

    # Check if the timeout was related to LLM operations
    if "no_provider_for_prompt" in error_str or "no provider" in error_str:
        return GenVMErrorCode.LLM_NO_PROVIDER

    if "rate limit" in error_str or "429" in error_str:
        return GenVMErrorCode.LLM_RATE_LIMITED

    if "status_not_ok" in error_str:
        return GenVMErrorCode.LLM_PROVIDER_ERROR

    if "fatal: true" in error_str:
        # This indicates an LLM-related fatal error
        if "llm" in error_str or "provider" in error_str:
            return GenVMErrorCode.LLM_NO_PROVIDER
        return GenVMErrorCode.LLM_PROVIDER_ERROR

    return GenVMErrorCode.GENVM_TIMEOUT
