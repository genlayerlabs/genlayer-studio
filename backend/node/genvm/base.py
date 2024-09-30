# backend/node/genvm/base.py

from functools import partial
import inspect
import re
import pickle
import base64
import sys
import traceback
import io
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from typing import Any, Callable

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.node.genvm.equivalence_principle import EquivalencePrinciple
from backend.node.genvm.code_enforcement import code_enforcement_check
from backend.node.genvm.std.vector_store import VectorStore
from backend.node.genvm.types import (
    PendingTransaction,
    Receipt,
    ExecutionResultStatus,
    ExecutionMode,
)
from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.protocol_rpc.message_handler.types import (
    LogEvent,
    EventType,
    EventScope,
)


@contextmanager
def safe_globals(override_globals: dict[str] = None):
    old_globals = globals().copy()
    globals().update(
        {
            "contract_runner": None,
            "VectorStore": VectorStore,
        }
    )
    if override_globals:
        globals().update(override_globals)
    try:
        yield
    finally:
        globals().clear()
        globals().update(old_globals)


class ContractRunner:
    def __init__(
        self,
        mode: ExecutionMode,
        node_config: dict,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
    ):
        self.mode = mode  # if the node is acting as "validator" or "leader"
        self.node_config = node_config  # provider, model, config, stake
        self.from_address = None  # the address of the transaction sender
        self.gas_used = 0  # the amount of gas used by the contract
        self.eq_num = 0  # keeps track of the eq principle number being executed
        self.eq_outputs = {
            ExecutionMode.LEADER.value: {}
        }  # the eq principle outputs for the leader and validators
        self.contract_snapshot_factory = contract_snapshot_factory


class GenVM:
    eq_principle = EquivalencePrinciple

    def __init__(
        self,
        snapshot: ContractSnapshot,
        validator_mode: str,
        validator: dict,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        msg_handler: MessageHandler = None,
    ):
        self.snapshot = snapshot
        self.validator_mode = validator_mode
        self.msg_handler = msg_handler
        self.contract_runner = ContractRunner(
            validator_mode, validator, contract_snapshot_factory
        )
        self.pending_transactions: list[PendingTransaction] = []

    @staticmethod
    def _get_contract_class_name(contract_code: str) -> str:
        pattern = r"class (\w+)\(IContract\):"
        matches = re.findall(pattern, contract_code)
        if len(matches) == 0:
            raise Exception("No class name found")
        return matches[0]

    def _generate_receipt(
        self,
        class_name: str,
        encoded_object: str,
        method_name: str,
        args: list[str],
        execution_result: ExecutionResultStatus,
        error: Exception,
    ) -> Receipt:
        return Receipt(
            class_name=class_name,
            method=method_name,
            args=args,
            gas_used=self.contract_runner.gas_used,
            mode=self.contract_runner.mode,
            contract_state=encoded_object,
            node_config=self.contract_runner.node_config,
            eq_outputs=self.contract_runner.eq_outputs,
            execution_result=execution_result,
            error=error,
            pending_transactions=self.pending_transactions,
        )

    async def deploy_contract(
        self,
        from_address: str,
        code_to_deploy: str,
        constructor_args: dict,
        leader_receipt: Receipt | None,
    ):
        class_name = self._get_contract_class_name(code_to_deploy)
        code_enforcement_check(code_to_deploy, class_name)
        self.contract_runner.from_address = from_address
        execution_result = ExecutionResultStatus.SUCCESS
        error = None

        self.eq_principle.contract_runner = self.contract_runner
        if self.contract_runner.mode == ExecutionMode.VALIDATOR:
            self.contract_runner.eq_outputs[ExecutionMode.LEADER.value] = (
                leader_receipt.eq_outputs[ExecutionMode.LEADER.value]
            )

        # Buffers to capture stdout and stderr
        stdout_buffer = io.StringIO()

        with redirect_stdout(stdout_buffer), safe_globals(
            {
                "contract_runner": self.contract_runner,
                "Contract": partial(
                    ExternalContract,
                    self.contract_runner.contract_snapshot_factory,
                    lambda x: self.pending_transactions.append(x),
                    self,
                ),
            }
        ):
            local_namespace = {}
            exec(code_to_deploy, globals(), local_namespace)

            contract_class = local_namespace[class_name]

            # Ensure the class and other necessary elements are in the global local_namespace if needed
            for name, value in local_namespace.items():
                globals()[name] = value

            module = sys.modules[__name__]
            setattr(module, class_name, contract_class)

            encoded_pickled_object = None  # Default value in order to have something to return in case of error
            try:
                # Manual instantiation of the class is done to handle async __init__ methods
                current_contract = contract_class.__new__(
                    contract_class, **constructor_args
                )
                if inspect.iscoroutinefunction(current_contract.__init__):
                    await current_contract.__init__(**constructor_args)
                else:
                    current_contract.__init__(**constructor_args)
                pickled_object = pickle.dumps(current_contract)
                encoded_pickled_object = base64.b64encode(pickled_object).decode(
                    "utf-8"
                )

            except Exception as e:
                trace = traceback.format_exc()
                error = e
                print("Error deploying contract", error)
                print(trace)
                execution_result = ExecutionResultStatus.ERROR
                self.msg_handler.send_message(
                    LogEvent(
                        "contract_deployment_failed",
                        EventType.ERROR,
                        EventScope.GENVM,
                        "Error deploying contract: " + str(error),
                        {
                            "error": str(error),
                            "traceback": f"\n{trace}",
                        },
                    )
                )

            ## Clean up
            delattr(module, class_name)

        if self.contract_runner.mode == ExecutionMode.LEADER:
            captured_stdout = stdout_buffer.getvalue()

            if captured_stdout:
                print(captured_stdout)
                self.send_stdout(captured_stdout, self.msg_handler)

            if execution_result == ExecutionResultStatus.SUCCESS:
                self.msg_handler.send_message(
                    LogEvent(
                        "deploying_contract",
                        EventType.SUCCESS,
                        EventScope.GENVM,
                        "Deploying contract",
                        {
                            "constructor_args": constructor_args,
                            "output": captured_stdout,
                        },
                    )
                )

        return self._generate_receipt(
            class_name,
            encoded_pickled_object,
            "__init__",
            [constructor_args],
            execution_result,
            error,
        )

    async def run_contract(
        self,
        from_address: str,
        function_name: str,
        args: list,
        leader_receipt: Receipt | None,
    ) -> Receipt:
        self.contract_runner.from_address = from_address
        contract_code = self.snapshot.contract_code
        execution_result = ExecutionResultStatus.SUCCESS
        error = None

        self.eq_principle.contract_runner = self.contract_runner

        if self.contract_runner.mode == ExecutionMode.VALIDATOR:
            self.contract_runner.eq_outputs[ExecutionMode.LEADER.value] = (
                leader_receipt.eq_outputs[ExecutionMode.LEADER.value]
            )

        # Buffers to capture stdout and stderr
        stdout_buffer = io.StringIO()

        with redirect_stdout(stdout_buffer), safe_globals(
            {
                "contract_runner": self.contract_runner,
                "Contract": partial(
                    ExternalContract,
                    self.contract_runner.contract_snapshot_factory,
                    lambda x: self.pending_transactions.append(x),
                    self,
                ),
            }
        ):
            local_namespace = {}
            # Execute the code to ensure all classes are defined in the local_namespace
            exec(contract_code, globals(), local_namespace)

            # Ensure the class and other necessary elements are in the global local_namespace if needed
            globals().update(local_namespace)

            contract_encoded_state = self.snapshot.encoded_state
            decoded_pickled_object = base64.b64decode(contract_encoded_state)
            current_contract = pickle.loads(decoded_pickled_object)

            function_to_run = getattr(current_contract, function_name, None)

            try:
                if inspect.iscoroutinefunction(function_to_run):
                    await function_to_run(*args)
                else:
                    function_to_run(*args)
            except Exception as e:
                trace = traceback.format_exc()
                error = e
                print("Error executing method", error)
                print(trace)
                execution_result = ExecutionResultStatus.ERROR
                self.msg_handler.send_message(
                    LogEvent(
                        "write_contract_failed",
                        EventType.ERROR,
                        EventScope.GENVM,
                        "Error executing method " + function_name + ": " + str(error),
                        {
                            "method_name": function_name,
                            "method_args": args,
                            "error": str(error),
                            "traceback": f"\n{trace}",
                        },
                    )
                )

            pickled_object = pickle.dumps(current_contract)
            encoded_pickled_object = base64.b64encode(pickled_object).decode("utf-8")
            class_name = self._get_contract_class_name(contract_code)

        if self.contract_runner.mode == ExecutionMode.LEADER:
            captured_stdout = stdout_buffer.getvalue()

            if captured_stdout:
                print(captured_stdout)
                self.send_stdout(captured_stdout, self.msg_handler)

            if execution_result == ExecutionResultStatus.SUCCESS:
                self.msg_handler.send_message(
                    LogEvent(
                        "write_contract",
                        EventType.INFO,
                        EventScope.GENVM,
                        "Execute method: " + function_name,
                        {
                            "method_name": function_name,
                            "method_args": args,
                            "output": captured_stdout,
                        },
                    )
                )

        return self._generate_receipt(
            class_name,
            encoded_pickled_object,
            function_name,
            [args],
            execution_result,
            error,
        )

    @staticmethod
    def get_contract_schema(contract_code: str) -> dict:

        namespace = {}
        with safe_globals():
            exec(contract_code, globals(), namespace)
            class_name = GenVM._get_contract_class_name(contract_code)

            iclass = namespace[class_name]

            members = inspect.getmembers(iclass)

            # Find all class methods
            methods = {}
            functions_and_methods = [
                m for m in members if inspect.isfunction(m[1]) or inspect.ismethod(m[1])
            ]
            for name, member in functions_and_methods:
                signature = inspect.signature(member)

                inputs = {}
                for (
                    method_variable_name,
                    method_variable,
                ) in signature.parameters.items():
                    if method_variable_name != "self":
                        annotation = str(method_variable.annotation)[8:-2]
                        inputs[method_variable_name] = str(annotation)

                return_annotation = str(signature.return_annotation)[8:-2]

                if return_annotation == "inspect._empty":
                    return_annotation = "None"

                result = {"inputs": inputs, "output": return_annotation}

                methods[name] = result

            abi = GenVM.generate_abi_from_schema_methods(methods)

            contract_schema = {
                "class": class_name,
                "abi": abi,
            }

        return contract_schema

    @staticmethod
    def get_abi_param_type(param_type: str) -> str:
        if param_type == "int":
            return "uint256"
        if param_type == "str":
            return "string"
        if param_type == "bool":
            return "bool"
        if param_type == "dict":
            return "bytes"
        if param_type == "list":
            return "bytes"
        if param_type == "None":
            return "None"
        return param_type

    @staticmethod
    def generate_abi_from_schema_methods(contract_schema_methods: dict) -> list:
        abi = []

        for method_name, method_info in contract_schema_methods.items():
            abi_entry = {
                "name": method_name,
                "type": "function",
                "inputs": [],
                "outputs": [],
            }

            for input_name, input_type in method_info["inputs"].items():
                abi_entry["inputs"].append(
                    {"name": input_name, "type": GenVM.get_abi_param_type(input_type)}
                )

            if method_info["output"]:
                abi_entry["outputs"].append(
                    {
                        "name": "",
                        "type": GenVM.get_abi_param_type(method_info["output"]),
                    }
                )

            if method_name == "__init__":
                abi_entry["type"] = "constructor"
                del abi_entry["name"]
                del abi_entry["outputs"]

            abi.append(abi_entry)

        return abi

    @staticmethod
    def send_stdout(stdout: str, msg_handler: MessageHandler) -> str:
        msg_handler.send_message(
            LogEvent(
                "contract_stdout",
                EventType.INFO,
                EventScope.GENVM,
                stdout,
            ),
            log_to_terminal=False,
        )

    def get_contract_data(
        self,
        code: str,
        state: str,
        method_name: str,
        method_args: list,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
    ) -> Any:
        result = None
        decoded_pickled_object = base64.b64decode(state)
        output_buffer = io.StringIO()

        with redirect_stdout(output_buffer), redirect_stderr(
            output_buffer
        ), safe_globals(
            {
                "Contract": partial(
                    ExternalContract,
                    contract_snapshot_factory,
                    None,  # TODO: should read methods be allowed to add new transactions?
                    self,
                )
            }
        ):
            local_namespace = {}
            # Execute the code to ensure all classes are defined in the namespace
            exec(code, globals(), local_namespace)

            # Ensure the class and other necessary elements are in the global namespace if needed
            globals().update(local_namespace)

            contract_state = pickle.loads(decoded_pickled_object)
            method_to_call = getattr(contract_state, method_name)
            result = method_to_call(*method_args)

            captured_stdout = output_buffer.getvalue()

            if captured_stdout:
                print(captured_stdout)
                self.send_stdout(captured_stdout, self.msg_handler)

            if self.contract_runner.mode == ExecutionMode.LEADER:
                self.msg_handler.send_message(
                    LogEvent(
                        "read_contract",
                        EventType.INFO,
                        EventScope.GENVM,
                        "Call method: " + method_name,
                        {
                            "method_name": method_name,
                            "method_args": method_args,
                            "result": result,
                            "output": captured_stdout,
                        },
                    )
                )

            return result


class ExternalContract:
    def __init__(
        self,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        schedule_pending_transaction: Callable[[PendingTransaction], None],
        genvm: GenVM,
        address: str,
    ):
        self.address = address
        self.genvm = genvm
        self.contract_snapshot = contract_snapshot_factory(address)
        self.contract_snapshot_factory = contract_snapshot_factory
        self.schedule_pending_transaction = schedule_pending_transaction

    def __getattr__(self, name):
        def method(*args, **kwargs):
            if re.match("get_", name):
                return self.genvm.get_contract_data(
                    self.contract_snapshot.contract_code,
                    self.contract_snapshot.encoded_state,
                    name,
                    args,
                    self.contract_snapshot_factory,
                )
            else:
                self.schedule_pending_transaction(
                    PendingTransaction(
                        address=self.address, method_name=name, args=args
                    )
                )

            return None

        return method
