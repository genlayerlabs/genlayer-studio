"""
Common JSON-RPC exception classes for both Flask and FastAPI implementations.
"""

from typing import Any, Optional


class JSONRPCError(Exception):
    """
    JSON-RPC Error exception compatible with both Flask-JSONRPC and FastAPI.

    This provides a unified exception class that can be used across the entire
    codebase regardless of the underlying web framework.
    """

    def __init__(
        self,
        code: int = -32000,
        message: str = "Server error",
        data: Optional[Any] = None,
    ):
        """
        Initialize a JSON-RPC error.

        Args:
            code: Error code (default -32000 for application errors)
            message: Human-readable error message
            data: Additional error data (optional)
        """
        self.code = code
        self.message = message
        self.data = data
        super().__init__(self.message)

    def to_dict(self) -> dict:
        """Convert the error to a JSON-RPC error response format."""
        error_dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            error_dict["data"] = self.data
        return error_dict


# Standard JSON-RPC error codes
class ParseError(JSONRPCError):
    """Invalid JSON was received by the server."""

    def __init__(self, data: Optional[Any] = None):
        super().__init__(code=-32700, message="Parse error", data=data)


class InvalidRequest(JSONRPCError):
    """The JSON sent is not a valid Request object."""

    def __init__(self, data: Optional[Any] = None):
        super().__init__(code=-32600, message="Invalid Request", data=data)


class MethodNotFound(JSONRPCError):
    """The method does not exist / is not available."""

    def __init__(self, method: str, data: Optional[Any] = None):
        super().__init__(code=-32601, message=f"Method not found: {method}", data=data)


class InvalidParams(JSONRPCError):
    """Invalid method parameter(s)."""

    def __init__(self, message: str = "Invalid params", data: Optional[Any] = None):
        super().__init__(code=-32602, message=message, data=data)


class InternalError(JSONRPCError):
    """Internal JSON-RPC error."""

    def __init__(self, message: str = "Internal error", data: Optional[Any] = None):
        super().__init__(code=-32603, message=message, data=data)


# Application-specific error codes (as per JSON-RPC spec, -32000 to -32099)
class ServerError(JSONRPCError):
    """Generic server error."""

    def __init__(self, message: str = "Server error", data: Optional[Any] = None):
        super().__init__(code=-32000, message=message, data=data)
