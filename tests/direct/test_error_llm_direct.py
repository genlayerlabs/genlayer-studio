from pathlib import Path


def test_error_llm_contract_direct_mode_execute_prompt(
    direct_vm, direct_deploy, direct_alice
):
    """
    Direct-mode validation for ErrorLLMContract testcase=1 (exec_prompt under eq_principle).
    """
    contract_path = (
        Path(__file__).resolve().parent / "contracts" / "error_llm_contract_direct.py"
    )

    direct_vm.sender = direct_alice
    # direct mode auto-parses JSON strings into dicts; return a non-JSON wrapper so
    # the contract's .replace(...) logic still runs on a string.
    direct_vm.mock_llm(r"What is 2\+2\?", '```json\\n{"answer": 4}\\n```')

    direct_deploy(str(contract_path), 1)


def test_error_llm_contract_direct_mode_invalid_json_reverts(
    direct_vm, direct_deploy, direct_alice
):
    """
    Direct-mode validation for ErrorLLMContract testcase=3 (json.loads should fail).
    """
    contract_path = (
        Path(__file__).resolve().parent / "contracts" / "error_llm_contract_direct.py"
    )

    direct_vm.sender = direct_alice
    direct_vm.mock_llm(r"What is 2\+2\?", "not-json")

    # json.loads("not-json") => JSONDecodeError ("Expecting value: ...")
    with direct_vm.expect_revert("Expecting value"):
        direct_deploy(str(contract_path), 3)
