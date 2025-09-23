"""FastAPI integration layer for the RPC endpoint manager."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.protocol_rpc.exceptions import (
    InvalidRequest,
    MethodNotFound,
    ParseError,
)
from backend.protocol_rpc.rpc_endpoint_manager import (
    JSONRPCRequest,
    JSONRPCResponse,
    RPCEndpointManager,
)


class FastAPIRPCRouter:
    """Bridges FastAPI requests with the RPC endpoint manager."""

    def __init__(self, endpoint_manager: RPCEndpointManager) -> None:
        self._endpoint_manager = endpoint_manager

    async def handle_http_request(self, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            response = ParseError().to_dict()
            return JSONResponse(
                status_code=400,
                content=JSONRPCResponse(
                    jsonrpc="2.0", error=response, id=None
                ).model_dump(exclude_none=True),
            )

        if isinstance(payload, list):
            if not payload:
                invalid = InvalidRequest().to_dict()
                response = JSONRPCResponse(
                    jsonrpc="2.0", error=invalid, id=None
                ).model_dump(exclude_none=True)
                return JSONResponse(status_code=400, content=[response])

            responses: List[Dict[str, Any]] = []
            for entry in payload:
                responses.append(await self._dispatch_entry(entry, request=request))
            return JSONResponse(content=responses)

        if isinstance(payload, dict):
            response = await self._dispatch_entry(payload, request=request)
            return JSONResponse(content=response)

        invalid = InvalidRequest().to_dict()
        response = JSONRPCResponse(jsonrpc="2.0", error=invalid, id=None).model_dump(
            exclude_none=True
        )
        return JSONResponse(status_code=400, content=response)

    async def _dispatch_entry(
        self,
        payload: Dict[str, Any],
        *,
        request: Request,
    ) -> Dict[str, Any]:
        try:
            rpc_request = JSONRPCRequest(**payload)
        except ValidationError as exc:
            error = InvalidRequest(data={"errors": exc.errors()}).to_dict()
            return JSONRPCResponse(
                jsonrpc="2.0", error=error, id=payload.get("id")
            ).model_dump(exclude_none=True)

        if rpc_request.method == "ping":
            response = JSONRPCResponse(
                jsonrpc="2.0", result="OK", id=rpc_request.id
            ).model_dump(exclude_none=True)
            return response

        try:
            response = await self._endpoint_manager.invoke(rpc_request, request)
            return response.model_dump(exclude_none=True)
        except MethodNotFound as exc:
            return JSONRPCResponse(
                jsonrpc="2.0", error=exc.to_dict(), id=rpc_request.id
            ).model_dump(exclude_none=True)
