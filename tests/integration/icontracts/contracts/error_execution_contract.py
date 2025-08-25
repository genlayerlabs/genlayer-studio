# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *


class ErrorExecutionContract(gl.Contract):
    state: TreeMap[str, str]

    def __init__(self, testcase: int, target_address: str | None = None):

        if testcase == 1:
            self.test_type_error()
        elif testcase == 2:
            self.test_index_error()
        elif testcase == 3:
            self.test_key_error()
        elif testcase == 4:
            self.test_zero_division()
        elif testcase == 5:
            self.test_value_error()
        elif testcase == 6:
            self.test_memory_allocation()
        elif testcase == 7:
            self.test_stack_overflow()
        elif testcase == 8:
            self.test_infinite_loop()
        elif testcase == 9:
            self.test_invalid_bytecode()
        elif testcase == 10:
            self.test_corrupt_state()
        elif testcase == 11:
            self.test_corrupt_state_value()
        elif testcase == 12:
            self.test_cross_contract_call(target_address)

    def test_type_error(self) -> None:
        # Testing type error with string + number
        result = "hello"
        result += 1  # This should raise a type error

    def test_index_error(self) -> None:
        # Testing index error with list access
        my_list = [1, 2, 3]
        value = my_list[5]  # This should raise an index error

    def test_key_error(self) -> None:
        # Testing key error with dict access
        my_dict = {"a": 1, "b": 2}
        value = my_dict["c"]  # This should raise a key error

    def test_zero_division(self) -> None:
        # Testing division by zero
        x = 10
        y = x / 0  # This should raise a zero division error

    def test_value_error(self) -> None:
        # Testing value error with int("hello")
        value = int("hello")  # This should raise a value error

    def test_memory_allocation(self) -> None:
        # Create a huge list that should exceed memory limits
        huge_list = [i for i in range(10**8)]
        self.state["huge"] = str(huge_list)

    def test_stack_overflow(self) -> None:
        """Cause stack overflow with deep recursion"""

        def recursive_fn(n: int) -> int:
            return recursive_fn(n + 1)

        recursive_fn(0)

    def test_infinite_loop(self) -> None:
        """Create an infinite loop"""
        x = 0
        while True:
            x += 1

    def test_invalid_bytecode(self) -> None:
        """Try to execute invalid bytecode"""
        # This should create invalid bytecode when compiled
        exec("invalid python code")

    def test_corrupt_state(self) -> None:
        """Try to corrupt contract state"""
        self.state = 1

    def test_corrupt_state_value(self) -> None:
        """Try to corrupt contract state"""
        self.state["key"] = 1

    def test_cross_contract_call(self, target_address: str) -> None:
        """Test invalid cross-contract calls"""
        # Try to call a non-existent method on another contract
        gl.get_contract_at(Address(target_address)).non_existent_method()
