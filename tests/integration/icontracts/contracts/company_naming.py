# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from genlayer import *

MAX_SCORE_DIFFERENCE = 3


class CompanyNaming(gl.Contract):
    scores: TreeMap[str, u256]

    def __init__(self):
        pass

    @gl.public.write
    def score_alignment(self, company_name: str, description: str) -> int:
        """Score how well a company name aligns with its description."""

        task = f"""
You are an expert business analyst specializing in brand alignment. Your task is to analyze how well a company name aligns with its description and provide a score from 0 to 10.

Scoring criteria:
1. Relevance (0-2 points)
- Does the name reflect the company's industry or purpose?

2. Memorability (0-2 points)
- Is the name distinctive and easy to remember?

3. Description Match (0-4 points)
- How well does the name capture the key elements in the description?
- Does it reflect the company's unique value proposition?

4. Brand Potential (0-2 points)
- Does the combination of name and description create a cohesive brand identity?

Company Name: {company_name}
Description: {description}

Output format - Respond using ONLY the following format:
{{
"analysis": str detailed analysis of name-description alignment,
"score": int between 0-10 being the overall alignment score>
}}
It is mandatory that you respond only using the JSON format above,
nothing else. Don't include any other words or characters,
your output must be only JSON without any formatting prefix or suffix.
This result should be perfectly parseable by a JSON parser without errors.
"""

        def leader_fn():
            result = gl.nondet.exec_prompt(task)
            result = _extract_json_from_string(result)
            result = json.loads(result)
            return result

        def validator_fn(
            leaders_res: gl.vm.Result,
        ) -> bool:
            validators_res = leader_fn()
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            return (
                abs(validators_res["score"] - leaders_res.calldata["score"])
                <= MAX_SCORE_DIFFERENCE
            )

        analysis = gl.vm.run_nondet(leader_fn, validator_fn)

        score = analysis["score"]
        self.scores[company_name] = score

        return score

    @gl.public.view
    def get_score(self, company_name: str) -> int:
        """Retrieve a previously computed score."""
        if company_name in self.scores:
            return self.scores[company_name]
        else:
            return 0


def _extract_json_from_string(s: str) -> str:
    start_index = s.find("{")
    end_index = s.rfind("}")
    if start_index != -1 and end_index != -1 and start_index < end_index:
        return s[start_index : end_index + 1]
    else:
        return ""
