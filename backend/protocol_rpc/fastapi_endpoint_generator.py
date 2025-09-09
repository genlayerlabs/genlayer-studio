"""
FastAPI endpoint generator for registering RPC methods.
Adapted from endpoint_generator.py for FastAPI compatibility.
"""

import inspect
import json
import typing
import collections.abc
import dataclasses
import base64
from functools import partial, wraps
from typing import Callable, Dict, Any, Optional

from backend.protocol_rpc.message_handler.fastapi_handler import MessageHandler


def get_json_rpc_method_name(function: Callable, method_name: str | None = None):
    if method_name is None:
        if isinstance(function, partial):
            return function.func.__name__
        else:
            return function.__name__
    return method_name


def _decode_exception(x: Exception) -> typing.Any:
    def unfold(x: typing.Any):
        if isinstance(x, tuple):
            return list(x)
        if isinstance(x, BaseException):
            import traceback

            res = {
                "message": str(x),
                "type": type(x).__name__,
                "traceback": traceback.format_exception(
                    type(x), x, x.__traceback__
                ),
            }
            if x.__cause__ is not None:
                res["cause"] = x.__cause__
            if x.__context__ is not None:
                res["context"] = x.__context__
            return res
        if isinstance(x, collections.abc.Buffer):
            return base64.b64encode(x).decode("ascii")
        if dataclasses.is_dataclass(x) and not isinstance(x, type):
            return dataclasses.asdict(x)
        return x

    try:
        return json.loads(json.dumps(x, default=unfold))
    except Exception:
        return repr(x)


def _serialize(obj):
    """
    Serialize the object to a JSON-compatible format.
    - Convert tuple to list
    - Serialize dict
    - Serialize object
    - Fallback to string
    """
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    elif isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]  # Convert tuple to list
    elif isinstance(obj, dict):
        return {_serialize(key): _serialize(value) for key, value in obj.items()}
    elif hasattr(obj, "__dict__"):
        return _serialize(obj.__dict__)
    else:  # Fallback
        return str(obj)


class FastAPIEndpointRegistry:
    """Registry for FastAPI RPC endpoints."""
    
    def __init__(self, msg_handler: MessageHandler):
        self.msg_handler = msg_handler
        self.methods: Dict[str, Callable] = {}
        self.method_metadata: Dict[str, Dict[str, Any]] = {}
    
    def generate_rpc_endpoint(
        self,
        partial_function: Callable,
        method_name: str = None,
    ) -> str:
        """
        Generate and register an RPC endpoint for FastAPI.
        Returns the method name that was registered.
        """
        json_rpc_method_name = get_json_rpc_method_name(partial_function, method_name)
        
        # Store the partial function and its metadata
        self.methods[json_rpc_method_name] = partial_function
        
        # Store metadata about what parameters are already filled
        if isinstance(partial_function, partial):
            filled_args = partial_function.args if partial_function.args else ()
            filled_kwargs = partial_function.keywords if partial_function.keywords else {}
            underlying_func = partial_function.func
        else:
            filled_args = ()
            filled_kwargs = {}
            underlying_func = partial_function
        
        self.method_metadata[json_rpc_method_name] = {
            'filled_args': filled_args,
            'filled_kwargs': filled_kwargs,
            'underlying_func': underlying_func,
            'is_partial': isinstance(partial_function, partial)
        }
        
        return json_rpc_method_name
    
    async def handle_method(
        self,
        method_name: str,
        params: Optional[Any],
        app_state: Dict[str, Any],
        db: Any
    ) -> Any:
        """
        Handle execution of a registered RPC method.
        """
        if method_name not in self.methods:
            raise ValueError(f"Method {method_name} not found")
        
        handler = self.methods[method_name]
        metadata = self.method_metadata[method_name]
        
        # Get the signature of the underlying function
        underlying_func = metadata['underlying_func']
        sig = inspect.signature(underlying_func)
        
        # Build kwargs for dependencies that haven't been filled by partial
        kwargs = {}
        
        if metadata['is_partial']:
            # For partial functions, skip parameters that are already filled
            filled_args_count = len(metadata['filled_args'])
            filled_kwargs = metadata['filled_kwargs']
            
            param_list = list(sig.parameters.items())
            
            # Skip filled positional arguments and add unfilled dependencies
            for i, (param_name, param) in enumerate(param_list):
                # Skip if it's a positional arg that was filled
                if i < filled_args_count:
                    continue
                # Skip if it's a keyword arg that was filled
                if param_name in filled_kwargs:
                    continue
                
                # Add dependency injection for unfilled parameters
                if param_name == 'session' or param_name == 'request_session':
                    kwargs[param_name] = db
                elif param_name == 'msg_handler':
                    kwargs[param_name] = app_state.get('msg_handler')
                elif param_name == 'accounts_manager':
                    kwargs[param_name] = app_state.get('accounts_manager')
                elif param_name == 'transactions_processor':
                    kwargs[param_name] = app_state.get('transactions_processor')
                elif param_name == 'validators_registry':
                    kwargs[param_name] = app_state.get('validators_registry')
                elif param_name == 'validators_manager':
                    kwargs[param_name] = app_state.get('validators_manager')
                elif param_name == 'llm_provider_registry':
                    kwargs[param_name] = app_state.get('llm_provider_registry')
                elif param_name == 'consensus':
                    kwargs[param_name] = app_state.get('consensus')
                elif param_name == 'consensus_service':
                    kwargs[param_name] = app_state.get('consensus_service')
                elif param_name == 'snapshot_manager':
                    kwargs[param_name] = app_state.get('snapshot_manager')
                elif param_name == 'transactions_parser':
                    kwargs[param_name] = app_state.get('transactions_parser')
                elif param_name == 'sqlalchemy_db':
                    kwargs[param_name] = app_state.get('sqlalchemy_db')
        else:
            # For non-partial functions, inject all dependencies
            for param_name in sig.parameters:
                if param_name == 'session' or param_name == 'request_session':
                    kwargs[param_name] = db
                elif param_name == 'msg_handler':
                    kwargs[param_name] = app_state.get('msg_handler')
                elif param_name in app_state:
                    kwargs[param_name] = app_state.get(param_name)
        
        # Call the handler with params
        try:
            if isinstance(params, list):
                # Positional arguments
                result = handler(*params, **kwargs)
            elif isinstance(params, dict):
                # Named arguments - merge with kwargs
                merged_kwargs = {**kwargs, **params}
                result = handler(**merged_kwargs)
            elif params is None:
                # No parameters
                result = handler(**kwargs)
            else:
                # Single parameter
                result = handler(params, **kwargs)
            
            # Handle async functions
            if inspect.iscoroutinefunction(underlying_func):
                result = await result
            elif hasattr(result, "__await__"):
                result = await result
            
            return _serialize(result)
            
        except Exception as e:
            # Log the error with the message handler
            self.msg_handler.error(f"Error in {method_name}: {str(e)}")
            raise


def register_endpoints_for_fastapi(
    msg_handler: MessageHandler,
    request_session,
    accounts_manager,
    transactions_processor,
    validators_registry,
    validators_manager,
    consensus,
    consensus_service,
    llm_provider_registry,
    snapshot_manager,
    transactions_parser,
    sqlalchemy_db=None,
) -> FastAPIEndpointRegistry:
    """
    Register all endpoints for FastAPI and return the registry.
    This replaces the Flask-JSONRPC registration.
    """
    from backend.protocol_rpc import endpoints
    
    registry = FastAPIEndpointRegistry(msg_handler)
    
    # Helper function to register endpoints
    def register(func, method_name=None):
        return registry.generate_rpc_endpoint(func, method_name)
    
    # Register all endpoints (same as in endpoints.py register_endpoints)
    register(endpoints.ping)
    register(partial(endpoints.clear_db_tables, request_session), "sim_clearDbTables")
    register(partial(endpoints.fund_account, accounts_manager, transactions_processor), "sim_fundAccount")
    register(partial(endpoints.get_providers_and_models, llm_provider_registry, validators_manager), "sim_getProvidersAndModels")
    register(partial(endpoints.reset_defaults_llm_providers, llm_provider_registry), "sim_resetDefaultsLlmProviders")
    register(partial(endpoints.add_provider, llm_provider_registry), "sim_addProvider")
    register(partial(endpoints.update_provider, llm_provider_registry), "sim_updateProvider")
    register(partial(endpoints.delete_provider, llm_provider_registry), "sim_deleteProvider")
    register(partial(endpoints.create_validator, validators_manager), "sim_createValidator")
    register(partial(endpoints.create_random_validator, validators_manager), "sim_createRandomValidator")
    register(partial(endpoints.create_random_validators, validators_manager), "sim_createRandomValidators")
    register(partial(endpoints.update_validator, validators_manager), "sim_updateValidator")
    register(partial(endpoints.delete_validator, validators_manager), "sim_deleteValidator")
    register(partial(endpoints.delete_all_validators, validators_manager), "sim_deleteAllValidators")
    register(partial(endpoints.get_all_validators, validators_registry), "sim_getAllValidators")
    register(partial(endpoints.get_validator, validators_registry), "sim_getValidator")
    register(partial(endpoints.count_validators, validators_registry), "sim_countValidators")
    register(partial(endpoints.get_transactions_for_address, transactions_processor, accounts_manager), "sim_getTransactionsForAddress")
    register(partial(endpoints.set_finality_window_time, consensus), "sim_setFinalityWindowTime")
    register(partial(endpoints.get_finality_window_time, consensus), "sim_getFinalityWindowTime")
    register(partial(endpoints.get_contract, accounts_manager), "sim_getConsensusContract")
    register(partial(endpoints.create_snapshot, snapshot_manager), "sim_createSnapshot")
    register(partial(endpoints.restore_snapshot, snapshot_manager, msg_handler, consensus, validators_manager), "sim_restoreSnapshot")
    register(partial(endpoints.delete_all_snapshots, snapshot_manager), "sim_deleteAllSnapshots")
    
    # GenLayer endpoints
    register(partial(endpoints.get_contract_schema, accounts_manager, msg_handler), "gen_getContractSchema")
    register(partial(endpoints.get_contract_schema_for_code, msg_handler), "gen_getContractSchemaForCode")
    register(partial(endpoints.get_contract_code, request_session), "gen_getContractCode")
    register(partial(endpoints.gen_call, consensus, msg_handler), "gen_call")
    register(partial(endpoints.sim_call, consensus, msg_handler), "sim_call")
    
    # Ethereum-compatible endpoints
    register(partial(endpoints.get_balance, accounts_manager), "eth_getBalance")
    register(partial(endpoints.get_transaction_by_hash, transactions_processor), "eth_getTransactionByHash")
    register(partial(endpoints.eth_call, consensus, msg_handler), "eth_call")
    register(partial(endpoints.send_raw_transaction, transactions_processor, msg_handler, accounts_manager, transactions_parser, consensus_service), "eth_sendRawTransaction")
    register(partial(endpoints.get_transaction_count, transactions_processor), "eth_getTransactionCount")
    register(endpoints.get_chain_id, "eth_chainId")
    register(endpoints.get_net_version, "net_version")
    register(partial(endpoints.get_block_number, transactions_processor), "eth_blockNumber")
    register(partial(endpoints.get_block_by_number, transactions_processor), "eth_getBlockByNumber")
    register(endpoints.get_gas_price, "eth_gasPrice")
    register(endpoints.get_gas_estimate, "eth_estimateGas")
    register(partial(endpoints.get_transaction_receipt, transactions_processor), "eth_getTransactionReceipt")
    register(partial(endpoints.get_block_by_hash, transactions_processor), "eth_getBlockByHash")
    
    # Dev endpoints
    if sqlalchemy_db:
        register(partial(endpoints.dev_get_pool_status, sqlalchemy_db), "dev_getPoolStatus")
    
    return registry