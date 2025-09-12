# rpc/endpoint_generator.py

import typing
import collections.abc
import base64
import json
import dataclasses
from typing import Callable
from flask_jsonrpc import JSONRPC
import flask
from backend.protocol_rpc.exceptions import JSONRPCError
from functools import partial, wraps
import requests
import os
import traceback
from backend.protocol_rpc.aio import run_in_main_server_loop
from backend.protocol_rpc.message_handler.base import MessageHandler


def get_json_rpc_method_name(function: Callable, method_name: str | None = None):
    if method_name is None:
        if isinstance(function, partial):
            return function.func.__name__
        else:
            return function.__name__
    return method_name


def get_function_annotations(function: Callable) -> Callable:
    original_function_annotations = (
        function.func.__annotations__
        if isinstance(function, partial)
        else function.__annotations__
    )
    return {k: v for k, v in original_function_annotations.items()}


def _decode_exception(x: Exception) -> typing.Any:
    def unfold(x: typing.Any):
        if isinstance(x, tuple):
            return list(x)
        if isinstance(x, BaseException):
            import traceback

            res = {
                "kind": "exception",
                "args": x.args,
                "traceback": traceback.format_exception(x),
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


def setup_eth_method_handler(jsonrpc: JSONRPC):
    """Forwards eth_ methods to Hardhat if no own implementation is available"""
    app = jsonrpc.app
    port = os.environ.get("HARDHAT_PORT")
    url = os.environ.get("HARDHAT_URL")
    HARDHAT_URL = f"{url}:{port}"

    @app.before_request
    def handle_eth_methods():
        if flask.request.is_json and flask.request.path == "/api":
            try:
                request_json = flask.request.get_json()
                if not request_json:
                    return None

                # Handle batch requests (list of requests)
                if isinstance(request_json, list):
                    # Check if batch contains only eth_ methods without local implementation
                    site = jsonrpc.get_jsonrpc_site()
                    all_eth_forward = True

                    for req in request_json:
                        if isinstance(req, dict):
                            method = req.get("method", "")
                            # If it's not an eth_ method or we have a local implementation, can't forward all
                            if (
                                not method.startswith("eth_")
                                or method in site.view_funcs
                            ):
                                all_eth_forward = False
                                break
                        else:
                            all_eth_forward = False
                            break

                    # If all requests are eth_ methods without local implementation, forward entire batch
                    if all_eth_forward:
                        try:
                            with requests.Session() as http:
                                result = http.post(
                                    HARDHAT_URL,
                                    json=request_json,
                                    headers={"Content-Type": "application/json"},
                                )
                                return flask.Response(
                                    result.content,
                                    status=result.status_code,
                                    headers=dict(result.headers),
                                )
                        except requests.RequestException:
                            # Log the exception with traceback
                            app.logger.exception(
                                "Error forwarding batch request to Hardhat"
                            )

                            # Build JSON-RPC compliant error responses
                            error_responses = []
                            for req in request_json:
                                error_response = {
                                    "jsonrpc": "2.0",
                                    "id": (
                                        req.get("id") if isinstance(req, dict) else None
                                    ),
                                    "error": {
                                        "code": -32000,  # Server error
                                        "message": "Network error",
                                        "data": "An internal error occurred while forwarding the request to Hardhat.",
                                    },
                                }
                                error_responses.append(error_response)

                            # Return JSON-RPC error array with 200 status
                            return flask.jsonify(error_responses), 200

                    # Mixed batch or has local implementations - let Flask-JSONRPC handle it
                    # Flask-JSONRPC will process each request and forward unknowns to Hardhat
                    return None

                # Handle single request
                method = request_json.get("method", "")
                if method.startswith("eth_"):
                    site = jsonrpc.get_jsonrpc_site()
                    print(site.view_funcs)
                    print(method)
                    print(method in site.view_funcs)
                    return None  # Use local implementation
                    # if method in site.view_funcs:
                    # else:
                    #     # No local implementation, forward to Hardhat
                    #     try:
                    #         with requests.Session() as http:
                    #             result = http.post(
                    #                 HARDHAT_URL,
                    #                 json=request_json,
                    #                 headers={"Content-Type": "application/json"},
                    #             ).json()

                    #             if "error" in result:
                    #                 raise JSONRPCError(
                    #                     code=result["error"].get("code", -32000),
                    #                     message=result["error"].get(
                    #                         "message", "Hardhat node error"
                    #                     ),
                    #                     data=result["error"].get("data", {}),
                    #                 )

                    #             return result

                    #     except requests.RequestException:
                    #         # Log the exception with traceback
                    #         app.logger.exception(
                    #             "Error forwarding single request to Hardhat"
                    #         )
                    #         raise JSONRPCError(
                    #             code=-32000,  # Server error
                    #             message="Network error",
                    #             data="An internal error occurred while forwarding the request to Hardhat.",
                    #         )

            except Exception:
                # Log the exception with traceback
                app.logger.exception("Error in before_request handler")
        return None  # Continue normal processing for non-eth methods


def generate_rpc_endpoint(
    jsonrpc: JSONRPC,
    msg_handler: MessageHandler,
    partial_function: Callable,
    method_name: str = None,
) -> Callable:
    json_rpc_method_name = get_json_rpc_method_name(partial_function, method_name)
    partial_function.__name__ = json_rpc_method_name
    partial_function.__annotations__ = get_function_annotations(partial_function)

    @wraps(partial_function)
    async def endpoint(*endpoint_args, **endpoint_kwargs):
        try:
            result = partial_function(*endpoint_args, **endpoint_kwargs)
            if hasattr(result, "__await__"):
                result = await run_in_main_server_loop(result)
            return _serialize(result)
        except JSONRPCError as e:
            raise e
        except Exception as e:
            raise JSONRPCError(
                code=-32000,
                message=str(e),
                data={"error": _decode_exception(e)},
            )

    endpoint = msg_handler.log_endpoint_info(endpoint)
    endpoint = jsonrpc.method(json_rpc_method_name)(endpoint)

    return endpoint


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
