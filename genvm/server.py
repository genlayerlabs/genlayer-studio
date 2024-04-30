import os
import ast
import subprocess
import json
import inspect
from flask import Flask
from flask_jsonrpc import JSONRPC
from genvm.utils import debug_output, transaction_files, save_files

from dotenv import load_dotenv

load_dotenv()

app = Flask("genvm_api")
jsonrpc = JSONRPC(app, "/api", enable_web_browsable_api=True)


def execute_transaction() -> dict:
    pass


@jsonrpc.method("leader_executes_transaction")
def leader_executes_transaction(icontract: str, node_config: dict) -> dict:

    return_data = {"status": "error", "data": None}

    icontract_file, recipt_file, _, _ = transaction_files()

    save_files(icontract, node_config, "leader")

    try:
        result = subprocess.run(
            ["python", icontract_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except Exception as e:
        return_data["data"] = str(e)
        return return_data

    debug_output("LLM Result", result)

    if result.returncode != 0:
        return_data["data"] = str(result.returncode) + ": " + str(result.stderr)
        return return_data

    # Access the output of the subprocess.run command
    file = open(recipt_file, "r")
    contents = json.load(file)
    file.close()

    # TODO: Leader needs to be the name of the VM
    debug_output("leader_executes_transaction(response)", contents)

    # os.remove(recipt_file)

    return_data["status"] = "success"
    return_data["data"] = contents
    return return_data


@jsonrpc.method("validator_executes_transaction")
def validator_executes_transaction(
    icontract: str, node_config: dict, leader_recipt: dict
) -> dict:

    return_data = {"status": "error", "data": None}

    icontract_file, recipt_file, _, leader_recipt_file = transaction_files()

    save_files(icontract, node_config, "validator", leader_recipt)

    try:
        result = subprocess.run(
            ["python", icontract_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except Exception as e:
        return_data["data"] = str(e)
        return return_data

    debug_output("LLM Result", result)

    if result.returncode != 0:
        return_data["data"] = str(result.returncode) + ": " + str(result.stderr)
        return return_data

    # Access the output of the subprocess.run command
    file = open(recipt_file, "r")
    contents = json.load(file)
    file.close()

    debug_output("validator_executes_transaction(response)", contents)

    # os.remove(leader_recipt_file)

    return_data["status"] = "success"
    return_data["data"] = contents
    return return_data


@jsonrpc.method("get_icontract_schema")
def get_icontract_schema(icontract: str) -> dict:

    debug_output("icontract", icontract)

    class_name = None
    namespace = {}
    exec(icontract, globals(), namespace)
    for class_name_in_contract, class_type_in_contract in namespace.items():
        if "WrappedClass" in str(class_type_in_contract):
            class_name = class_name_in_contract

    if not class_name:
        raise Exception("This contract does not have a class declaration")

    iclass = namespace[class_name]

    members = inspect.getmembers(iclass)

    # Find all class methods
    methods = {}
    functions_and_methods = [
        m for m in members if inspect.isfunction(m[1]) or inspect.ismethod(m[1])
    ]
    for name, member in functions_and_methods:
        if not name.startswith("_"):
            signature = inspect.signature(member)

            inputs = {}
            for method_variable_name, method_variable in signature.parameters.items():
                if method_variable_name != "self":
                    annotation = str(method_variable.annotation)[8:-2]
                    inputs[method_variable_name] = str(annotation)

            return_annotation = str(signature.return_annotation)[8:-2]

            if return_annotation == "inspect._empty":
                return_annotation = "None"

            methods[name] = {"inputs": inputs, "output": return_annotation}

    # Find all class variables
    variables = {}
    tree = ast.parse(icontract)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign):
                    if hasattr(stmt.annotation, "id") and hasattr(stmt.target, "id"):
                        variables[stmt.target.id] = stmt.annotation.id

    return {"class": class_name, "methods": methods, "variables": variables}


if __name__ == "__main__":
    app.run(debug=True, port=os.environ.get("GENVMPORT"), host="0.0.0.0")
