"""JSON-RPC endpoint dispatcher backed by FastAPI dependencies."""

from __future__ import annotations

import inspect
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import params
from fastapi.dependencies.utils import get_dependant, solve_dependencies
from fastapi.requests import Request
from pydantic import BaseModel, ConfigDict

from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.protocol_rpc.exceptions import (
    InternalError,
    InvalidParams,
    JSONRPCError,
    MethodNotFound,
)
from backend.protocol_rpc.message_handler.fastapi_handler import MessageHandler
from backend.protocol_rpc.message_handler.types import EventScope, EventType, LogEvent


@dataclass(slots=True)
class LogPolicy:
    log_request: bool = True
    log_success: bool = True
    log_failure: bool = True


@dataclass(slots=True)
class RPCEndpointDefinition:
    name: str
    handler: Any
    description: Optional[str] = None
    log_policy: LogPolicy = field(default_factory=LogPolicy)


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Any | None = None
    id: Any | None = None


class JSONRPCResponse(BaseModel):
    model_config = ConfigDict(exclude_none=True)

    jsonrpc: str = "2.0"
    result: Any | None = None
    error: Optional[Dict[str, Any]] = None
    id: Any | None = None


@dataclass(slots=True)
class RegisteredEndpoint:
    definition: RPCEndpointDefinition
    dependant: Any
    user_parameters: List[inspect.Parameter]


class RPCEndpointManager:
    """Executes RPC handlers using FastAPI's dependency resolution."""

    def __init__(
        self,
        logger: MessageHandler,
        dependency_overrides_provider: Any,
    ) -> None:
        self._logger = logger
        self._dependency_overrides_provider = dependency_overrides_provider
        self._endpoints: Dict[str, RegisteredEndpoint] = {}

    def register(self, definition: RPCEndpointDefinition) -> None:
        if definition.name in self._endpoints:
            raise ValueError(f"RPC method already registered: {definition.name}")

        signature = inspect.signature(definition.handler)
        user_parameters: List[inspect.Parameter] = []

        for parameter in signature.parameters.values():
            if isinstance(parameter.default, params.Depends):
                continue
            user_parameters.append(parameter)

        dependant = get_dependant(
            path=f"/rpc/{definition.name}", call=definition.handler
        )

        self._endpoints[definition.name] = RegisteredEndpoint(
            definition=definition,
            dependant=dependant,
            user_parameters=user_parameters,
        )

    def has_method(self, name: str) -> bool:
        return name in self._endpoints

    async def invoke(
        self,
        request: JSONRPCRequest,
        fastapi_request: Request,
    ) -> JSONRPCResponse:
        registered = self._endpoints.get(request.method)
        if not registered:
            raise MethodNotFound(request.method)

        definition = registered.definition
        should_log = self._should_log(request.method, definition.log_policy)

        if should_log and definition.log_policy.log_request:
            self._logger.send_message(
                LogEvent(
                    name="endpoint_call",
                    type=EventType.INFO,
                    scope=EventScope.RPC,
                    message=f"RPC method called: {request.method}",
                    data={"method": request.method, "params": request.params},
                )
            )

        try:
            result = await self._call_endpoint(registered, request, fastapi_request)
            response = JSONRPCResponse(jsonrpc="2.0", result=result, id=request.id)

            if should_log and definition.log_policy.log_success:
                self._logger.send_message(
                    LogEvent(
                        name="endpoint_success",
                        type=EventType.SUCCESS,
                        scope=EventScope.RPC,
                        message=f"RPC method completed: {request.method}",
                        data={"method": request.method},
                    )
                )
            return response
        except JSONRPCError as exc:
            if should_log and definition.log_policy.log_failure:
                stack_trace = traceback.format_exc()
                print(f"JSONRPCError in {request.method}:\n{stack_trace}")
                self._logger.send_message(
                    LogEvent(
                        name="endpoint_error",
                        type=EventType.ERROR,
                        scope=EventScope.RPC,
                        message=f"Error in {request.method}: {exc.message}",
                        data={
                            "method": request.method,
                            "code": exc.code,
                            "error_data": getattr(exc, "data", None),
                            "traceback": stack_trace,
                        },
                    )
                )
            return JSONRPCResponse(jsonrpc="2.0", error=exc.to_dict(), id=request.id)
        except Exception as exc:  # pragma: no cover - safety net
            if should_log and definition.log_policy.log_failure:
                stack_trace = traceback.format_exc()
                print(f"Unexpected error in {request.method}:\n{stack_trace}")
                self._logger.send_message(
                    LogEvent(
                        name="endpoint_error",
                        type=EventType.ERROR,
                        scope=EventScope.RPC,
                        message=f"Unexpected error in {request.method}: {exc}",
                        data={"method": request.method, "traceback": stack_trace},
                    )
                )
            internal = InternalError(message=str(exc))
            return JSONRPCResponse(
                jsonrpc="2.0", error=internal.to_dict(), id=request.id
            )

    async def _call_endpoint(
        self,
        registered: RegisteredEndpoint,
        request: JSONRPCRequest,
        fastapi_request: Request,
    ) -> Any:

        try:
            bound_arguments = self._bind_rpc_arguments(registered, request.params)
        except InvalidParams as exc:
            self._logger.send_message(
                LogEvent(
                    name="invalid_params_arguments",
                    type=EventType.ERROR,
                    scope=EventScope.RPC,
                    message=f"Argument binding failed for {request.method}: {exc}",
                    data={
                        "method": request.method,
                        "params": request.params,
                    },
                )
            )
            raise

        synthetic_body = bound_arguments or {}

        solved = await solve_dependencies(
            request=fastapi_request,
            dependant=registered.dependant,
            body=synthetic_body,
            dependency_overrides_provider=self._dependency_overrides_provider,
        )
        values, errors = solved[0], solved[1]

        if errors:
            user_param_names = {param.name for param in registered.user_parameters}
            filtered_errors = []
            for error in errors:
                loc = error.get("loc") if isinstance(error, dict) else None
                if isinstance(loc, (tuple, list)) and loc:
                    location_scope = loc[0]
                    parameter_name = loc[-1] if len(loc) > 1 else None
                    if location_scope in {"query", "body", "form"} and (
                        parameter_name in user_param_names or len(loc) == 1
                    ):
                        continue
                filtered_errors.append(error)

            if filtered_errors:
                self._logger.send_message(
                    LogEvent(
                        name="invalid_params_dependency",
                        type=EventType.ERROR,
                        scope=EventScope.RPC,
                        message=f"Dependency resolution failed for {request.method}",
                        data={
                            "method": request.method,
                            "errors": filtered_errors,
                            "params": request.params,
                        },
                    )
                )
                raise InvalidParams(message=str(filtered_errors))

        call_kwargs = dict(values)
        call_kwargs.update(bound_arguments)

        result = registered.dependant.call(**call_kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    def _bind_rpc_arguments(
        self,
        registered: RegisteredEndpoint,
        params: Any,
    ) -> Dict[str, Any]:
        user_parameters = registered.user_parameters
        user_param_names = [param.name for param in user_parameters]

        if params is None:
            provided: Dict[str, Any] = {}
        elif isinstance(params, list):
            provided = {}
            if len(params) > len(user_parameters):
                raise InvalidParams(message="Too many parameters provided")

            for value, parameter in zip(params, user_parameters):
                provided[parameter.name] = value

            missing_parameters = user_parameters[len(params) :]
            for parameter in missing_parameters:
                if parameter.default is inspect._empty:
                    raise InvalidParams(
                        message=f"Missing required parameter: {parameter.name}"
                    )
        elif isinstance(params, dict):
            provided = {}
            for key, value in params.items():
                if key not in user_param_names:
                    raise InvalidParams(message=f"Unexpected parameter: {key}")
                provided[key] = value
        else:
            if len(user_parameters) != 1:
                raise InvalidParams(
                    message="Positional params require a single argument"
                )
            provided = {user_parameters[0].name: params}

        for parameter in user_parameters:
            if parameter.name not in provided and parameter.default is inspect._empty:
                raise InvalidParams(
                    message=f"Missing required parameter: {parameter.name}"
                )

        return provided

    def _should_log(self, method: str, policy: LogPolicy) -> bool:
        if not policy.log_request and not policy.log_success and not policy.log_failure:
            return False
        disabled = GlobalConfiguration.get_disabled_info_logs_endpoints()
        return method not in disabled
