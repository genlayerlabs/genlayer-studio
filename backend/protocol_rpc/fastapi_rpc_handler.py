"""
FastAPI RPC handler that uses the existing endpoints.py implementations.
This avoids duplicating all the endpoint logic.
"""

import asyncio
import inspect
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Import all the endpoint functions from the existing endpoints.py
from backend.protocol_rpc import endpoints

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
    """Handler for RPC methods using existing endpoints.py implementations."""
    
    def __init__(self):
        self.methods = {}
        self._register_methods()
    
    def _register_methods(self):
        """Register all RPC methods from endpoints.py."""
        
        # Map RPC method names to endpoint functions
        method_map = {
            # Helper endpoints
            "ping": endpoints.ping,
            
            # Simulator endpoints
            "sim_clearDbTables": endpoints.clear_db_tables,
            "sim_fundAccount": endpoints.fund_account,
            "sim_getProvidersAndModels": endpoints.get_providers_and_models,
            "sim_resetDefaultsLlmProviders": endpoints.reset_defaults_llm_providers,
            "sim_addProvider": endpoints.add_provider,
            "sim_updateProvider": endpoints.update_provider,
            "sim_deleteProvider": endpoints.delete_provider,
            "sim_createValidator": endpoints.create_validator,
            "sim_createRandomValidator": endpoints.create_random_validator,
            "sim_createRandomValidators": endpoints.create_random_validators,
            "sim_updateValidator": endpoints.update_validator,
            "sim_deleteValidator": endpoints.delete_validator,
            "sim_deleteAllValidators": endpoints.delete_all_validators,
            "sim_getAllValidators": endpoints.get_all_validators,
            "sim_getValidator": endpoints.get_validator,
            "sim_countValidators": endpoints.count_validators,
            "sim_getTransactionsForAddress": endpoints.get_transactions_for_address,
            "sim_setFinalityWindowTime": endpoints.set_finality_window_time,
            "sim_getFinalityWindowTime": endpoints.get_finality_window_time,
            "sim_getConsensusContract": endpoints.get_contract,
            "sim_createSnapshot": endpoints.create_snapshot,
            "sim_restoreSnapshot": endpoints.restore_snapshot,
            "sim_deleteAllSnapshots": endpoints.delete_all_snapshots,
            
            # GenLayer endpoints
            "gen_getContractSchema": endpoints.get_contract_schema,
            "gen_getContractSchemaForCode": endpoints.get_contract_schema_for_code,
            "gen_getContractCode": endpoints.get_contract_code,
            "gen_call": endpoints.gen_call,
            "sim_call": endpoints.sim_call,
            
            # Ethereum-compatible endpoints
            "eth_getBalance": endpoints.get_balance,
            "eth_getTransactionByHash": endpoints.get_transaction_by_hash,
            "eth_call": endpoints.eth_call,
            "eth_sendRawTransaction": endpoints.send_raw_transaction,
            "eth_getTransactionCount": endpoints.get_transaction_count,
            "eth_chainId": endpoints.get_chain_id,
            "net_version": endpoints.get_net_version,
            "eth_blockNumber": endpoints.get_block_number,
            "eth_getBlockByNumber": endpoints.get_block_by_number,
            "eth_gasPrice": endpoints.get_gas_price,
            "eth_estimateGas": endpoints.get_gas_estimate,
            "eth_getTransactionReceipt": endpoints.get_transaction_receipt,
            "eth_getBlockByHash": endpoints.get_block_by_hash,
            
            # Dev endpoints
            "dev_getPoolStatus": endpoints.dev_get_pool_status,
        }
        
        for method_name, handler in method_map.items():
            self.register_method(method_name, handler)
    
    def register_method(self, name: str, handler):
        """Register an RPC method handler."""
        self.methods[name] = handler
    
    async def handle_request(
        self,
        request: JSONRPCRequest,
        db: Session,
        app_state: Dict[str, Any]
    ) -> JSONRPCResponse:
        """Handle a JSON-RPC request."""
        
        # Check if method exists
        if request.method not in self.methods:
            return JSONRPCResponse(
                jsonrpc="2.0",
                error={
                    "code": -32601,
                    "message": f"Method not found: {request.method}"
                },
                id=request.id
            )
        
        try:
            # Get the handler
            handler = self.methods[request.method]
            
            # Get the function signature to determine what dependencies it needs
            sig = inspect.signature(handler)
            kwargs = {}
            
            # Map parameters to the function's expected arguments
            for param_name, param in sig.parameters.items():
                if param_name == 'session':
                    kwargs['session'] = db
                elif param_name == 'request_session':
                    kwargs['request_session'] = db
                elif param_name == 'msg_handler':
                    kwargs['msg_handler'] = app_state.get('msg_handler')
                elif param_name == 'accounts_manager':
                    kwargs['accounts_manager'] = app_state.get('accounts_manager')
                elif param_name == 'transactions_processor':
                    kwargs['transactions_processor'] = app_state.get('transactions_processor')
                elif param_name == 'validators_registry':
                    kwargs['validators_registry'] = app_state.get('validators_registry')
                elif param_name == 'validators_manager':
                    kwargs['validators_manager'] = app_state.get('validators_manager')
                elif param_name == 'llm_provider_registry':
                    kwargs['llm_provider_registry'] = app_state.get('llm_provider_registry')
                elif param_name == 'consensus':
                    kwargs['consensus'] = app_state.get('consensus')
                elif param_name == 'consensus_service':
                    kwargs['consensus_service'] = app_state.get('consensus_service')
                elif param_name == 'snapshot_manager':
                    kwargs['snapshot_manager'] = app_state.get('snapshot_manager')
                elif param_name == 'transactions_parser':
                    kwargs['transactions_parser'] = app_state.get('transactions_parser')
                elif param_name == 'sqlalchemy_db':
                    kwargs['sqlalchemy_db'] = app_state.get('sqlalchemy_db')
            
            # Call the handler with params and dependencies
            if isinstance(request.params, list):
                # Handle positional arguments
                # Filter kwargs to only include what the function expects
                filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
                
                # Call with positional params first, then kwargs
                if inspect.iscoroutinefunction(handler):
                    result = await handler(*request.params, **filtered_kwargs)
                else:
                    result = handler(*request.params, **filtered_kwargs)
                    
            elif isinstance(request.params, dict):
                # Handle named arguments
                # Merge params with dependencies
                all_kwargs = {**request.params, **kwargs}
                # Filter to only what the function expects
                filtered_kwargs = {k: v for k, v in all_kwargs.items() if k in sig.parameters}
                
                if inspect.iscoroutinefunction(handler):
                    result = await handler(**filtered_kwargs)
                else:
                    result = handler(**filtered_kwargs)
            else:
                # No params, just dependencies
                filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
                
                if inspect.iscoroutinefunction(handler):
                    result = await handler(**filtered_kwargs)
                else:
                    result = handler(**filtered_kwargs)
            
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

# Create global RPC handler instance
rpc_handler = RPCHandler()