from pathlib import Path

import pytest


def test_genvm_smoke_direct_mode(direct_vm, direct_deploy, direct_alice):
    """
    Direct-mode validation for the GenVM smoke contract.

    This avoids simulator overhead and validates the contract logic itself,
    while mocking nondet web + LLM calls.
    """
    contract_path = (
        Path(__file__).resolve().parents[1]
        / "integration"
        / "icontracts"
        / "contracts"
        / "genvm_smoke_v1.py"
    )

    direct_vm.sender = direct_alice

    direct_vm.mock_web(
        r"example\.com",
        {
            "response": {
                "status": 200,
                "headers": {},
                "body": b"Example Domain",
            },
            "method": "GET",
        },
    )
    direct_vm.mock_llm(r"Respond with exactly the two characters", "OK")

    contract = direct_deploy(str(contract_path))

    assert "example" in contract.get_web_data().lower()
    assert contract.get_prompt_result().strip() == "OK"
