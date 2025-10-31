"""Decorator-based RPC endpoint registration utilities."""

from __future__ import annotations

from typing import Any, Callable, Iterable, List, Optional

from backend.protocol_rpc.rpc_endpoint_manager import (
    LogPolicy,
    RPCEndpointDefinition,
)


class RPCEndpointRegistry:
    def __init__(self) -> None:
        self._definitions: list[RPCEndpointDefinition] = []

    def method(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        log_policy: Optional[LogPolicy] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            definition = RPCEndpointDefinition(
                name=name,
                handler=func,
                description=description,
                log_policy=log_policy or LogPolicy(),
            )
            self._definitions.append(definition)
            return func

        return decorator

    def extend(self, definitions: Iterable[RPCEndpointDefinition]) -> None:
        self._definitions.extend(definitions)

    def to_list(self) -> List[RPCEndpointDefinition]:
        return list(self._definitions)


rpc = RPCEndpointRegistry()
