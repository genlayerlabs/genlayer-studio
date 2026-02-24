from gltest import get_contract_factory
from gltest.exceptions import DeploymentError
from ast import literal_eval
from backend.node.types import ExecutionResultStatus
from enum import Enum
import pytest

pytestmark = pytest.mark.error_handling


class ErrorType(Enum):
    TYPE_ERROR = "TypeError"
    INDEX_ERROR = "IndexError"
    KEY_ERROR = "KeyError"
    ZERO_DIVISION_ERROR = "ZeroDivisionError"
    VALUE_ERROR = "ValueError"
    MEMORY_ERROR = "MemoryError"
    RECURSION_ERROR = "RecursionError"
    INFINITE_LOOP = "InfiniteLoop"
    SYNTAX_ERROR = "SyntaxError"
    ASSERTION_ERROR = "AssertionError"
    ATTRIBUTE_ERROR_1 = "AttributeError"
    ATTRIBUTE_ERROR_2 = "AttributeError"

    def __int__(self) -> int:
        values = {
            ErrorType.TYPE_ERROR: 1,
            ErrorType.INDEX_ERROR: 2,
            ErrorType.KEY_ERROR: 3,
            ErrorType.ZERO_DIVISION_ERROR: 4,
            ErrorType.VALUE_ERROR: 5,
            ErrorType.MEMORY_ERROR: 6,
            ErrorType.RECURSION_ERROR: 7,
            ErrorType.INFINITE_LOOP: 8,
            ErrorType.SYNTAX_ERROR: 9,
            ErrorType.ASSERTION_ERROR: 10,
            ErrorType.ATTRIBUTE_ERROR_1: 11,
            ErrorType.ATTRIBUTE_ERROR_2: 12,
        }
        return values[self]


def _check_result(tx_receipt: dict, error_string: str):
    for i in range(2):
        receipt_leader = tx_receipt["consensus_data"]["leader_receipt"][i]
        assert ExecutionResultStatus.ERROR.value == receipt_leader["execution_result"]
        assert error_string in receipt_leader["genvm_result"]["stderr"]

    for i in range(4):
        receipt_validator = tx_receipt["consensus_data"]["validators"][i]
        assert (
            ExecutionResultStatus.ERROR.value == receipt_validator["execution_result"]
        )
        assert error_string in receipt_validator["genvm_result"]["stderr"]

    assert (
        tx_receipt["consensus_history"]["consensus_results"][-1]["consensus_round"]
        == "Accepted"
    )


def _check_last_round(tx_receipt: dict, expected_round: str):
    assert (
        tx_receipt["consensus_history"]["consensus_results"][-1]["consensus_round"]
        == expected_round
    )


def _deployment_error_to_tx_receipt(e: DeploymentError):
    error_dict_str = str(e).split("error: ", 1)[1]
    return literal_eval(error_dict_str)


def _run_testcase(setup_validators, testcase: ErrorType):
    """Test type error (string + number)"""
    setup_validators()
    factory = get_contract_factory("ErrorExecutionContract")

    try:
        if testcase == ErrorType.ATTRIBUTE_ERROR_2:
            contract_a = factory.deploy(args=[0])
            args = [int(testcase), contract_a.address]
        else:
            args = [int(testcase)]

        if testcase in [ErrorType.INFINITE_LOOP, ErrorType.MEMORY_ERROR]:
            factory.deploy(args=args, wait_interval=20000, wait_retries=30)
        else:
            factory.deploy(args=args)
    except DeploymentError as e:
        tx_receipt = _deployment_error_to_tx_receipt(e)

        if testcase == ErrorType.INFINITE_LOOP:
            _check_last_round(tx_receipt, "Leader Timeout")
        elif testcase == ErrorType.MEMORY_ERROR:
            receipt_leader = tx_receipt["consensus_data"]["leader_receipt"][0]
            assert (
                ExecutionResultStatus.ERROR.value == receipt_leader["execution_result"]
            )
            assert testcase.value in receipt_leader["genvm_result"]["stderr"]
        else:
            _check_result(tx_receipt, testcase.value)


def test_type_error(setup_validators):
    _run_testcase(setup_validators, ErrorType.TYPE_ERROR)


def test_index_error(setup_validators):
    _run_testcase(setup_validators, ErrorType.INDEX_ERROR)


def test_key_error(setup_validators):
    _run_testcase(setup_validators, ErrorType.KEY_ERROR)


def test_zero_division_error(setup_validators):
    _run_testcase(setup_validators, ErrorType.ZERO_DIVISION_ERROR)


def test_value_error(setup_validators):
    _run_testcase(setup_validators, ErrorType.VALUE_ERROR)


def test_memory_allocation_error(setup_validators):
    _run_testcase(setup_validators, ErrorType.MEMORY_ERROR)


def test_stack_overflow(setup_validators):
    _run_testcase(setup_validators, ErrorType.RECURSION_ERROR)


def test_infinite_loop(setup_validators):
    _run_testcase(setup_validators, ErrorType.INFINITE_LOOP)


def test_invalid_bytecode(setup_validators):
    _run_testcase(setup_validators, ErrorType.SYNTAX_ERROR)


def test_contract_state_corruption(setup_validators):
    _run_testcase(setup_validators, ErrorType.ASSERTION_ERROR)


def test_contract_state_value_corruption(setup_validators):
    _run_testcase(setup_validators, ErrorType.ATTRIBUTE_ERROR_1)


def test_cross_contract_call_errors(setup_validators):
    _run_testcase(setup_validators, ErrorType.ATTRIBUTE_ERROR_2)
