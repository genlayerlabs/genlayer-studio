# { "Depends": "py-genlayer:test" }

import json
from enum import Enum
from datetime import datetime, timezone
from urllib.parse import urlparse
from genlayer import *


class Status(Enum):
    ACTIVE = "Active"
    RESOLVED = "Resolved"
    ERROR = "Error"


class IntelligentOracle(gl.Contract):
    # Declare persistent storage fields
    prediction_market_id: str
    title: str
    description: str
    potential_outcomes: DynArray[str]
    rules: DynArray[str]
    data_source_domains: DynArray[str]
    resolution_urls: DynArray[str]
    earliest_resolution_date: str  # Store as ISO format string
    status: str  # Store as string since Enum isn't supported
    analysis: str  # Store analysis results
    outcome: str

    def __init__(
        self,
        prediction_market_id: str,
        title: str,
        description: str,
        potential_outcomes: list[str],
        rules: list[str],
        data_source_domains: list[str],
        resolution_urls: list[str],
        earliest_resolution_date: str,
    ):
        if (
            not prediction_market_id
            or not title
            or not description
            or not potential_outcomes
            or not rules
            or not earliest_resolution_date
        ):
            raise ValueError("Missing required fields.")

        if not resolution_urls and not data_source_domains:
            raise ValueError("Missing resolution URLs or data source domains.")

        if len(resolution_urls) > 0 and len(data_source_domains) > 0:
            raise ValueError(
                "Cannot provide both resolution URLs and data source domains."
            )

        if len(potential_outcomes) < 2:
            raise ValueError("At least two potential outcomes are required.")

        if len(potential_outcomes) != len(set(potential_outcomes)):
            raise ValueError("Potential outcomes must be unique.")

        self.prediction_market_id = prediction_market_id
        self.title = title
        self.description = description

        for outcome in potential_outcomes:
            self.potential_outcomes.append(outcome.strip())

        for rule in rules:
            self.rules.append(rule)

        for datasource in data_source_domains:
            self.data_source_domains.append(
                datasource.strip()
                .lower()
                .replace("http://", "")
                .replace("https://", "")
                .replace("www.", "")
            )

        for url in resolution_urls:
            self.resolution_urls.append(url.strip())

        self.earliest_resolution_date = earliest_resolution_date
        self.status = Status.ACTIVE.value

        self.outcome = ""

    @gl.public.view
    def _check_evidence_domain(self, evidence: str) -> bool:
        try:
            parsed_url = urlparse(evidence)
            evidence_domain = parsed_url.netloc.lower().replace("www.", "")
            return evidence_domain in self.data_source_domains
        except Exception:
            return False

    @gl.public.write
    def resolve(self, evidence_url: str = "") -> None:
        if self.status == Status.RESOLVED.value:
            raise ValueError("Cannot resolve an already resolved oracle.")

        current_time = datetime.now().astimezone().date()
        earliest_time = datetime.fromisoformat(self.earliest_resolution_date).date()
        if current_time < earliest_time:
            raise ValueError("Cannot resolve before the earliest resolution date.")

        if len(self.resolution_urls) > 0 and evidence_url:
            raise ValueError(
                "An evidence URL was provided but the oracle is configured to use resolution URLs already provided."
            )

        if len(self.resolution_urls) == 0 and not evidence_url:
            raise ValueError(
                "No evidence URL provided and the oracle is not configured to use resolution URLs."
            )

        if evidence_url:
            is_valid = self._check_evidence_domain(evidence_url)
            if not is_valid:
                raise ValueError(
                    "The evidence URL does not match any of the data source domains."
                )

        analyzed_outputs = []
        resources_to_check = (
            self.resolution_urls if len(self.resolution_urls) > 0 else [evidence_url]
        )

        title = self.title
        description = self.description
        potential_outcomes = list(self.potential_outcomes)
        rules = list(self.rules)
        earliest_resolution_date = self.earliest_resolution_date

        for resource_url in resources_to_check:

            def evaluate_single_source() -> str:
                resource_web_data = gl.get_webpage(resource_url, mode="text")
                print(resource_web_data)

                task = f"""
You are an AI Validator tasked with resolving a prediction market.
Your goal is to determine the correct outcome based on the user-defined rules,
the provided webpage HTML content, the resolution date, and the list of potential outcomes.

### Inputs
<title>
{title}
</title>

<description>
{description}
</description>

<potential_outcomes>
{potential_outcomes}
</potential_outcomes>

<rules>
{rules}
</rules>

<source_url>
{resource_url}
</source_url>

<webpage_content>
{resource_web_data}
</webpage_content>

<current_date>
{datetime.now().astimezone()}
</current_date>

<earliest_resolution_date>
{earliest_resolution_date}
</earliest_resolution_date>




### **Your Task:**
1. **Analyze the Inputs:**
- Carefully read and interpret the user-defined rules.
- Parse the HTML content to extract meaningful information relevant to the rules.
- Determine if the source pertains to the event that is being predicted.
- Determine if the event has occurred yet.

2. **Provide Reasoning:**
- Write a clear, self-contained reasoning for the outcome.
- Reference specific parts of the rules and the extracted data that support your decision.
- Ensure that someone reading the reasoning can understand it without needing additional information.

3. **Determine The Outcome:**
- Based on your analysis, decide which potential outcome is correct.
- If an outcome can be determined, but the outcome is not in the list of potential outcomes, the outcome should be `ERROR`.
- If the information is insufficient or inconclusive, or the event has not occurred yet, and you cannot confidently determine an outcome based on this source, the outcome should be `UNDETERMINED`.




### **Output Format:**

Provide your response in **valid JSON** format with the following structure:

```json
{{
    "valid_source": "true | false",
    "event_has_occurred": "true | false",
    "reasoning": "Your detailed reasoning here",
    "outcome": "Chosen outcome from the potential outcomes list, `UNDETERMINED` if no outcome can be determined based on this source, `ERROR` if the outcome is not in the potential outcomes list"
}}
```

### **Constraints and Considerations:**

- **Accuracy:** Base your decision strictly on the provided inputs.
- **Objectivity:** Remain neutral and unbiased.
- **Clarity:** Make sure your reasoning is easy to understand.
- **Validity:** Ensure the JSON output is properly formatted and free of errors. Do not include trailing commas.
                """
                result = gl.exec_prompt(task)
                print(result)
                return result

            result = gl.eq_principle_prompt_comparative(
                evaluate_single_source,
                principle="`outcome` field must be exactly the same. All other fields must be similar",
            )

            result_dict = _parse_json_dict(result)
            analyzed_outputs.append((resource_url, result_dict))

        def evaluate_all_sources() -> str:
            task = f"""
    You are an AI Validator tasked with resolving a prediction market Oracle. Your goal is to determine
    the correct outcome based on processed data from all of the individial data sources. Here are your inputs

    ### Inputs
    <title>
    {title}
    </title>

    <description>
    {description}
    </description>

    <potential_outcomes>
    {potential_outcomes}
    </potential_outcomes>

    <rules>
    {rules}
    </rules>

    <processed_data>
    {analyzed_outputs}
    </processed_data>

    <current_date>
    {datetime.now().astimezone()}
    </current_date>

    <earliest_resolution_date>
    {earliest_resolution_date}
    </earliest_resolution_date>

    ### **Your Task:**
    1. **Analyze the Inputs:**
    - Carefully read and interpret the user-defined rules.
    - Take into account all the processed data form the sources.
    - Consider the resolution date in your analysis to ensure timeliness of the data.

    2. **Determine The Outcome:**
    - The output should be determined from the processed data form the resolution sources.
    - Based on your analysis, decide which potential outcome is correct.
    - If an outcome can be determined, but the outcome is not in the list of potential outcomes, the outcome should be `ERROR`.
    - If the information is insufficient or inconclusive, and you cannot confidently determine an outcome, the outcome should be `UNDETERMINED`.
    - Your response should reflect a coherent summary outcome from the previous analysis.
    - If multiple sources contradict each other, refer to the rules to determine how to resolve the contradiction.
    - If the rules do not provide a clear resolution, the outcome should be `ERROR`.

    ### **Output Format:**

    Provide your response in **valid JSON** format with the following structure:

    ```json
    {{
    "relevant_sources": "List of URLs that are relevant to the outcome",
    "reasoning": "Your detailed reasoning here",
    "outcome": "Chosen outcome from the potential outcomes list, `UNDETERMINED` if undetermined, `ERROR` if the outcome is not in the potential outcomes list"
    }}
    ```

    ### **Constraints and Considerations:**

    - **Accuracy:** Base your decision strictly on the provided inputs.
    - **Objectivity:** Remain neutral and unbiased.
    - **Clarity:** Make sure your reason is easy to understand.
    - **Validity:** Ensure the JSON output is properly formatted and free of errors. Do not include trailing commas.

            """

            result = gl.exec_prompt(task)
            print(result)
            return result

        result = gl.eq_principle_prompt_comparative(
            evaluate_all_sources,
            principle="`outcome` field must be exactly the same. All other fields must be similar",
        )

        result_dict = _parse_json_dict(result)
        self.analysis = json.dumps(result_dict)

        if result_dict["outcome"] == "UNDETERMINED":
            return

        if (
            result_dict["outcome"] == "ERROR"
            or result_dict["outcome"] not in self.potential_outcomes
        ):
            self.status = Status.ERROR.value
            return

        self.outcome = result_dict["outcome"]
        self.status = Status.RESOLVED.value

    @gl.public.view
    def get_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "description": self.description,
            "potential_outcomes": list(self.potential_outcomes),
            "rules": list(self.rules),
            "data_source_domains": list(self.data_source_domains),
            "resolution_urls": list(self.resolution_urls),
            "status": self.status,
            "earliest_resolution_date": self.earliest_resolution_date,
            "analysis": self.analysis,
            "outcome": self.outcome,
            "prediction_market_id": self.prediction_market_id,
        }

    @gl.public.view
    def get_status(self) -> str:
        return self.status


def _parse_json_dict(json_str: str) -> dict:
    """
    Used to sanitize the JSON output from the LLM.
    Remove everything before the first '{' and after the last '}', and remove trailing commas before closing braces/brackets
    """
    first_brace = json_str.find("{")
    last_brace = json_str.rfind("}")
    json_str = json_str[first_brace : last_brace + 1]

    # Remove trailing commas before closing braces/brackets
    import re

    json_str = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", json_str)
    print(json_str)

    return json.loads(json_str)
