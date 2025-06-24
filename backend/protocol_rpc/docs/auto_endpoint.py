"""
Automatic JSON-RPC Endpoint Registration and Documentation System

This module provides decorators to automatically register and document JSON-RPC endpoints
with minimal boilerplate code.
"""

import inspect
from typing import Any, Callable, Dict, List, Optional, get_type_hints
from dataclasses import dataclass
from enum import Enum


class EndpointCategory(Enum):
    """Endpoint categories for automatic documentation grouping"""

    SIMULATOR = "sim"
    GENLAYER = "gen"
    ETHEREUM = "eth"
    NETWORK = "net"
    UTILITY = "util"


@dataclass
class EndpointInfo:
    """Information about a registered endpoint"""

    name: str
    function: Callable
    category: EndpointCategory
    description: str
    param_descriptions: Dict[str, str]
    access_control: bool = False
    dependencies: Optional[List[str]] = None


class AutoEndpointRegistry:
    """Registry for automatically documented endpoints"""

    def __init__(self):
        self.endpoints: Dict[str, EndpointInfo] = {}

    def register(
        self,
        category: EndpointCategory,
        name: Optional[str] = None,
        description: Optional[str] = None,
        param_descriptions: Optional[Dict[str, str]] = None,
        dependencies: Optional[List[str]] = None,
        restricted: bool = False,
    ):
        """
        Decorator to automatically register and document an endpoint.

        Args:
            category: The endpoint category (sim, gen, eth, etc.)
            name: Custom method name (auto-generated if not provided)
            description: Method description (uses docstring if not provided)
            param_descriptions: Dictionary of parameter descriptions
            dependencies: List of dependency parameter names to inject
            restricted: Whether this endpoint is restricted in hosted environments
        """

        def decorator(func: Callable) -> Callable:
            # Generate method name if not provided
            method_name = name or f"{category.value}_{func.__name__}"

            # Use docstring as description if not provided
            method_description = description or (func.__doc__ or "").strip()

            # Store endpoint info
            endpoint_info = EndpointInfo(
                name=method_name,
                function=func,
                category=category,
                description=method_description,
                param_descriptions=param_descriptions or {},
                access_control=restricted,
                dependencies=dependencies or [],
            )

            self.endpoints[method_name] = endpoint_info

            # Add metadata to function for later use
            setattr(func, "_rpc_endpoint_info", endpoint_info)

            return func

        return decorator

    def get_all_endpoints(self) -> Dict[str, EndpointInfo]:
        """Get all registered endpoints"""
        return self.endpoints.copy()


# Global registry instance
endpoint_registry = AutoEndpointRegistry()


def rpc_endpoint(
    category: EndpointCategory,
    name: Optional[str] = None,
    description: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    dependencies: Optional[List[str]] = None,
    restricted: bool = False,
):
    """
    Simplified decorator for JSON-RPC endpoints.

    Example usage:

    @rpc_endpoint(
        category=EndpointCategory.SIMULATOR,
        description="Fund an account with tokens",
        params={
            "address": "The account address to fund",
            "amount": "Amount of tokens in wei"
        },
        dependencies=["accounts_manager", "transactions_processor"]
    )
    def fund_account(accounts_manager, transactions_processor, address: str, amount: int) -> str:
        # Implementation here
        pass
    """
    return endpoint_registry.register(
        category=category,
        name=name,
        description=description,
        param_descriptions=params,
        dependencies=dependencies,
        restricted=restricted,
    )


def simulator_endpoint(
    name: Optional[str] = None,
    description: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    dependencies: Optional[List[str]] = None,
    restricted: bool = False,
):
    """Shorthand decorator for simulator endpoints"""
    return rpc_endpoint(
        category=EndpointCategory.SIMULATOR,
        name=name,
        description=description,
        params=params,
        dependencies=dependencies,
        restricted=restricted,
    )


def genlayer_endpoint(
    name: Optional[str] = None,
    description: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    dependencies: Optional[List[str]] = None,
    restricted: bool = False,
):
    """Shorthand decorator for GenLayer endpoints"""
    return rpc_endpoint(
        category=EndpointCategory.GENLAYER,
        name=name,
        description=description,
        params=params,
        dependencies=dependencies,
        restricted=restricted,
    )


def ethereum_endpoint(
    name: Optional[str] = None,
    description: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    dependencies: Optional[List[str]] = None,
    restricted: bool = False,
):
    """Shorthand decorator for Ethereum-compatible endpoints"""
    return rpc_endpoint(
        category=EndpointCategory.ETHEREUM,
        name=name,
        description=description,
        params=params,
        dependencies=dependencies,
        restricted=restricted,
    )


def generate_documentation(jsonrpc):
    """
    Enhanced documentation generator that uses the auto-registered endpoint metadata.
    """
    from .generator import RPCDocsGenerator

    # Create enhanced generator with dynamic inheritance
    class EnhancedDocsGenerator(RPCDocsGenerator):
        """Enhanced documentation generator with auto-endpoint support"""

        def __init__(self):
            super().__init__()
            self.auto_endpoints: Dict[str, EndpointInfo] = {}

        def add_endpoint_info(self, method_name: str, endpoint_info: EndpointInfo):
            """Add endpoint info from auto-registration"""
            self.auto_endpoints[method_name] = endpoint_info

            # Update descriptions from auto-registered info
            if endpoint_info.description:
                self.METHOD_DESCRIPTIONS[method_name] = endpoint_info.description

            # Update parameter descriptions
            self.PARAM_DESCRIPTIONS.update(endpoint_info.param_descriptions)

            # Create MethodDoc with parameters from decorator
            try:
                # Import here to avoid circular imports
                import sys

                if "backend.protocol_rpc.docs.generator" in sys.modules:
                    from .generator import MethodDoc, ParameterDoc
                else:
                    # Generator not loaded yet, defer creation
                    return

                # Convert param_descriptions to ParameterDoc objects
                parameters = []
                for param_name, param_desc in endpoint_info.param_descriptions.items():
                    # Try to get better type information from function signature
                    param_type = self._infer_parameter_type(
                        endpoint_info.function, param_name
                    )

                    param_doc = ParameterDoc(
                        name=param_name,
                        type_str=param_type,
                        required=True,  # Default to required
                        description=param_desc,
                    )
                    parameters.append(param_doc)

                # Create and store the MethodDoc
                method_doc = MethodDoc(
                    name=method_name,
                    description=endpoint_info.description,
                    parameters=parameters,
                    returns="Any",  # Default return type
                    category=self._get_method_category(method_name),
                    examples=self._generate_examples(method_name, parameters),
                )

                self.methods[method_name] = method_doc

            except Exception as e:
                # Fallback: just store the basic info
                print(
                    f"Warning: Could not create full MethodDoc for {method_name}: {e}"
                )
                if endpoint_info.description:
                    self.METHOD_DESCRIPTIONS[method_name] = endpoint_info.description

        def _infer_parameter_type(self, func: Callable, param_name: str) -> str:
            """Infer parameter type from function signature"""
            try:
                sig = inspect.signature(func)
                if param_name in sig.parameters:
                    param = sig.parameters[param_name]
                    if param.annotation != inspect.Parameter.empty:
                        return self._get_type_string(param.annotation)
                return "str"  # Default fallback
            except Exception:
                return "str"  # Safe fallback

        def _extract_parameters(self, func: Callable):
            """Enhanced parameter extraction using auto-endpoint metadata"""
            # Check if this function has auto-endpoint metadata
            if hasattr(func, "_rpc_endpoint_info"):
                endpoint_info = getattr(func, "_rpc_endpoint_info")

                # Use the original function for parameter extraction
                original_func = endpoint_info.function
                sig = inspect.signature(original_func)
                type_hints = (
                    get_type_hints(original_func)
                    if hasattr(original_func, "__annotations__")
                    else {}
                )

                params = []
                param_list = list(sig.parameters.items())

                # Skip dependency parameters
                skip_count = (
                    len(endpoint_info.dependencies) if endpoint_info.dependencies else 0
                )
                for i, (name, param) in enumerate(param_list):
                    # Skip dependency parameters
                    if i < skip_count:
                        continue

                    # Skip 'self' parameter
                    if name == "self":
                        continue

                    type_hint = type_hints.get(name, Any)
                    type_str = self._get_type_string(type_hint)

                    from .generator import ParameterDoc

                    param_doc = ParameterDoc(
                        name=name,
                        type_str=type_str,
                        required=param.default == inspect.Parameter.empty,
                        default=(
                            None
                            if param.default == inspect.Parameter.empty
                            else param.default
                        ),
                        description=endpoint_info.param_descriptions.get(name, ""),
                    )
                    params.append(param_doc)

                return params

            # Fall back to parent implementation
            return super()._extract_parameters(func)

    generator = EnhancedDocsGenerator()

    # Add auto-registered endpoints
    for method_name, endpoint_info in endpoint_registry.get_all_endpoints().items():
        generator.add_endpoint_info(method_name, endpoint_info)

    # Analyze the JSONRPC app for any manually registered endpoints
    generator.analyze_jsonrpc_app(jsonrpc)

    return generator
