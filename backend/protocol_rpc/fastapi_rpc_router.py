"""FastAPI integration layer for the RPC endpoint manager."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect
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


MAX_BATCH_SIZE = 100


logger = logging.getLogger(__name__)


class FastAPIRPCRouter:
    """Bridges FastAPI requests with the RPC endpoint manager."""

    def __init__(self, endpoint_manager: RPCEndpointManager) -> None:
        self._endpoint_manager = endpoint_manager

    async def handle_http_request(self, request: Request) -> Response:
        try:
            payload = await request.json()
        except ClientDisconnect:
            logger.debug("Client disconnected before request body was read")
            return Response(status_code=204)
        except json.JSONDecodeError:
            # Malformed JSON payload. Always include id: null per JSON-RPC 2.0.
            error = ParseError().to_dict()
            return JSONResponse(
                status_code=400,
                content={"jsonrpc": "2.0", "error": error, "id": None},
            )

        if isinstance(payload, list):
            if not payload:
                # Empty batch is invalid per JSON-RPC 2.0, respond 400
                invalid = InvalidRequest().to_dict()
                return JSONResponse(
                    status_code=400,
                    content={"jsonrpc": "2.0", "error": invalid, "id": None},
                )

            if len(payload) > MAX_BATCH_SIZE:
                invalid = InvalidRequest(
                    data={
                        "message": f"Batch request exceeds maximum size of {MAX_BATCH_SIZE}",
                        "size": len(payload),
                    }
                ).to_dict()
                # Oversized batch also returns 400
                return JSONResponse(
                    status_code=400,
                    content={"jsonrpc": "2.0", "error": invalid, "id": None},
                )

            responses: List[Dict[str, Any]] = []
            for entry in payload:
                response = await self._dispatch_entry(entry, request=request)
                if isinstance(response, dict) and response.get("id") is not None:
                    responses.append(response)
            if not responses:
                return Response(status_code=204)
            return JSONResponse(content=responses)

        if isinstance(payload, dict):
            response = await self._dispatch_entry(payload, request=request)
            if isinstance(response, dict) and response.get("id") is None:
                return Response(status_code=204)
            return JSONResponse(content=response)

        invalid = InvalidRequest().to_dict()
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "error": invalid, "id": None},
        )

    async def _dispatch_entry(
        self,
        payload: Any,
        *,
        request: Request,
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            invalid = InvalidRequest().to_dict()
            # Ensure id is present and null when invalid
            return JSONRPCResponse(jsonrpc="2.0", error=invalid, id=None).model_dump(
                exclude_none=False
            )

        try:
            rpc_request = JSONRPCRequest(**payload)
        except ValidationError:
            logger.exception("Invalid JSON-RPC request payload failed validation")
            error = InvalidRequest().to_dict()
            return JSONRPCResponse(
                jsonrpc="2.0", error=error, id=payload.get("id")
            ).model_dump(exclude_none=False)

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
