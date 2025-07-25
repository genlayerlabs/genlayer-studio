# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json


class ErrorLLMContract(gl.Contract):
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

        result = gl.eq_principle.strict_eq(get_llm_answer)

    def test_system_error(self):
        prompt = "What is 2+2?"
        result = gl.nondet.exec_prompt(prompt)

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

        result = gl.eq_principle.strict_eq(get_llm_answer)
