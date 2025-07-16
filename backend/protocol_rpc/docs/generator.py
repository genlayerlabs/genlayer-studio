import inspect
import typing
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    get_type_hints,
    get_origin,
    get_args,
)
from dataclasses import dataclass
from functools import partial
import json

from flask_jsonrpc.app import JSONRPC


@dataclass
class ParameterDoc:
    """Documentation for a single parameter"""

    name: str
    type_str: str
    required: bool = True
    default: Any = None
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result = {"name": self.name, "type": self.type_str, "required": self.required}
        if self.default is not None:
            result["default"] = self.default
        if self.description:
            result["description"] = self.description
        return result


@dataclass
class MethodDoc:
    """Documentation for a JSON-RPC method"""

    name: str
    description: str = ""
    parameters: Optional[List[ParameterDoc]] = None
    returns: str = "Any"
    examples: Optional[List[Dict[str, Any]]] = None
    category: str = ""

    def __post_init__(self):
        if self.parameters is None:
            self.parameters = []
        if self.examples is None:
            self.examples = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "parameters": [p.to_dict() for p in (self.parameters or [])],
            "returns": self.returns,
            "examples": self.examples or [],
        }


class RPCDocsGenerator:
    """Minimal base documentation generator for auto-endpoint system"""

    # Empty dictionaries - auto-endpoints provide their own descriptions
    METHOD_DESCRIPTIONS = {}
    PARAM_DESCRIPTIONS = {}

    def __init__(self):
        self.methods: Dict[str, MethodDoc] = {}

    def _get_type_string(self, type_hint: Any) -> str:
        """Convert a type hint to a readable string"""
        if type_hint is type(None):
            return "null"
        if type_hint is Any or type_hint is inspect._empty:
            return "Any"

        origin = get_origin(type_hint)
        if origin is list or origin is List:
            args = get_args(type_hint)
            if args:
                return f"List[{self._get_type_string(args[0])}]"
            return "List"
        elif origin is dict or origin is Dict:
            args = get_args(type_hint)
            if len(args) >= 2:
                return f"Dict[{self._get_type_string(args[0])}, {self._get_type_string(args[1])}]"
            return "Dict"
        elif origin is Optional or origin is typing.Union:
            args = get_args(type_hint)
            non_none_args = [arg for arg in args if arg is not type(None)]
            if len(non_none_args) == 1:
                return f"Optional[{self._get_type_string(non_none_args[0])}]"
            return f"Union[{', '.join(self._get_type_string(arg) for arg in args)}]"

        if hasattr(type_hint, "__name__"):
            return type_hint.__name__

        return str(type_hint)

    def _extract_parameters(self, func: Callable) -> List[ParameterDoc]:
        """Extract parameters from a function signature (base implementation)"""
        # This is overridden by the enhanced auto-endpoint system
        return []

    def analyze_jsonrpc_app(self, jsonrpc: JSONRPC) -> Dict[str, MethodDoc]:
        """Analyze a Flask-JSONRPC app and extract method documentation"""
        site = jsonrpc.get_jsonrpc_site()

        for method_name, view_func in site.view_funcs.items():
            # Skip internal methods
            if method_name.startswith("rpc."):
                continue

            # Skip if method already exists from auto-registration
            if method_name in self.methods:
                continue

            # Get the actual function (unwrap decorators)
            func = view_func
            while hasattr(func, "__wrapped__"):
                func = getattr(func, "__wrapped__")

            # Extract documentation
            params = self._extract_parameters(func)
            return_type = self._get_return_type(func)

            method_doc = MethodDoc(
                name=method_name,
                description=self.METHOD_DESCRIPTIONS.get(
                    method_name, func.__doc__ or ""
                ),
                parameters=params,
                returns=return_type,
                category=self._get_method_category(method_name),
                examples=self._generate_examples(method_name, params),
            )

            self.methods[method_name] = method_doc

        return self.methods

    def _get_return_type(self, func: Callable) -> str:
        """Extract return type from function"""
        if isinstance(func, partial):
            func = func.func

        type_hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
        return_type = type_hints.get("return", Any)
        return self._get_type_string(return_type)

    def _get_method_category(self, method_name: str) -> str:
        """Categorize method based on its prefix"""
        if method_name.startswith("sim_"):
            return "Simulator"
        elif method_name.startswith("gen_"):
            return "GenLayer"
        elif method_name.startswith("eth_"):
            return "Ethereum Compatible"
        elif method_name.startswith("net_"):
            return "Network"
        else:
            return "Utility"

    def _generate_examples(
        self, method_name: str, params: List[ParameterDoc]
    ) -> List[Dict[str, Any]]:
        """Generate example requests for a method"""
        from .utils import generate_method_examples

        # Convert ParameterDoc objects to dictionaries for utility function
        param_dicts = []
        for param in params:
            param_dicts.append(
                {"name": param.name, "type": param.type_str, "required": param.required}
            )

        return generate_method_examples(method_name, param_dicts)

    def generate_html(self) -> str:
        """Generate HTML documentation with Swagger-like interface"""
        from .templates import get_html_template
        from .styles import get_swagger_styles
        from .javascript import get_documentation_javascript

        # Prepare the methods data as JSON
        methods_json = json.dumps([m.to_dict() for m in self.methods.values()])

        # Get the components
        html_template = get_html_template()
        styles = get_swagger_styles()
        javascript = get_documentation_javascript(methods_json)

        # Combine everything
        return html_template.format(styles=styles, javascript=javascript)
