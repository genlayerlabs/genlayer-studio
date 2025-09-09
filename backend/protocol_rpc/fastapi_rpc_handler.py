"""
FastAPI RPC handler that uses the endpoint generator for proper registration.
"""

import asyncio
import inspect
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Import the FastAPI endpoint generator
from backend.protocol_rpc.fastapi_endpoint_generator import register_endpoints_for_fastapi, FastAPIEndpointRegistry

# Request/Response models
class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Union[List[Any], Dict[str, Any], None] = None
    id: Optional[Union[str, int]] = None

class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None
    id: Optional[Union[str, int]] = None

class JSONRPCError(Exception):
    """JSON-RPC Error exception."""
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(self.message)


class RPCHandler:
    """Handler for RPC methods using the FastAPI endpoint generator."""
    
    def __init__(self, app_state: Dict[str, Any]):
        """Initialize the RPC handler with app state to register endpoints."""
        self.app_state = app_state
        self.registry: Optional[FastAPIEndpointRegistry] = None
        self._initialize_registry()
    
    def _initialize_registry(self):
        """Initialize the endpoint registry with all dependencies."""
        # Get all required dependencies from app_state
        self.registry = register_endpoints_for_fastapi(
            msg_handler=self.app_state.get('msg_handler'),
            request_session=None,  # Will be injected per request
            accounts_manager=self.app_state.get('accounts_manager'),
            transactions_processor=self.app_state.get('transactions_processor'),
            validators_registry=self.app_state.get('validators_registry'),
            validators_manager=self.app_state.get('validators_manager'),
            consensus=self.app_state.get('consensus'),
            consensus_service=self.app_state.get('consensus_service'),
            llm_provider_registry=self.app_state.get('llm_provider_registry'),
            snapshot_manager=self.app_state.get('snapshot_manager'),
            transactions_parser=self.app_state.get('transactions_parser'),
            sqlalchemy_db=self.app_state.get('sqlalchemy_db')
        )
    
    async def handle_request(
        self,
        request: JSONRPCRequest,
        db: Session,
        app_state: Dict[str, Any]
    ) -> JSONRPCResponse:
        """Handle a JSON-RPC request."""
        
        # Check if method exists
        if request.method not in self.registry.methods:
            return JSONRPCResponse(
                jsonrpc="2.0",
                error={
                    "code": -32601,
                    "message": f"Method not found: {request.method}"
                },
                id=request.id
            )
        
        try:
            # Use the registry's handle_method to execute the request
            result = await self.registry.handle_method(
                request.method,
                request.params,
                app_state,
                db
            )
            
            return JSONRPCResponse(
                jsonrpc="2.0",
                result=result,
                id=request.id
            )
            
        except JSONRPCError as e:
            return JSONRPCResponse(
                jsonrpc="2.0",
                error={
                    "code": e.code,
                    "message": e.message,
                    "data": e.data
                },
                id=request.id
            )
        except Exception as e:
            import traceback
            return JSONRPCResponse(
                jsonrpc="2.0",
                error={
                    "code": -32603,
                    "message": str(e),
                    "data": {"traceback": traceback.format_exc()}
                },
                id=request.id
            )

# Global RPC handler instance will be initialized in FastAPI server
rpc_handler = None