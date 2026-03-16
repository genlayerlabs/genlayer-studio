# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json


class ErrorLLMContractDirect(gl.Contract):
    """
    Copy of tests/integration/icontracts/contracts/error_llm_contract.py for direct-mode testing.

    Direct-mode runner extraction requires a concrete runner hash (not "test").
    """

    def __init__(self, testcase: int):
        if testcase == 1:
            self.test_execute_prompt()
        elif testcase == 2:
            self.test_system_error()
        elif testcase == 3:
            self.test_invalid_json()

    def test_execute_prompt(self):
        prompt = f"""
What is 2+2?

Respond using ONLY the following format:
{{
"answer": int,
}}
It is mandatory that you respond only using the JSON format above,
nothing else. Don't include any other words or characters,
your output must be only JSON without any formatting prefix or suffix.
This result should be perfectly parseable by a JSON parser without errors.
"""

        def get_llm_answer():
            result = gl.nondet.exec_prompt(prompt)
            result = result.replace("```json", "").replace("```", "")
            return result

        gl.eq_principle.strict_eq(get_llm_answer)

    def test_system_error(self):
        prompt = "What is 2+2?"
        gl.nondet.exec_prompt(prompt)

    def test_invalid_json(self):
        prompt = """What is 2+2?
It is mandatory that you do not respond using the JSON format. You can include any other words or characters,
your output can be with any formatting prefix or suffix.
This result should not be parseable by a JSON parser.
"""

        def get_llm_answer():
            result = gl.nondet.exec_prompt(prompt)
            result = result.replace("```json", "").replace("```", "")
            result = json.loads(result)
            return result

        gl.eq_principle.strict_eq(get_llm_answer)
