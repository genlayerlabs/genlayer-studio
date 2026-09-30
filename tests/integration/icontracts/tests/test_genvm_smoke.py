from gltest import get_contract_factory


def test_genvm_smoke_contract(setup_validators):
    """
    Simulator-level GenVM smoke test.

    This is intentionally small: it validates that web rendering + LLM calls
    can execute inside a contract and that the deployed contract is readable.
    """
    mock_web_response = {
        "nondet_web_render": {
            "https://example.com/": {
                "mode": "text",
                "status": 200,
                "body": "Example Domain",
            },
        },
    }
    setup_validators(mock_web_response=mock_web_response)
    factory = get_contract_factory("GenVMSmoke")
    contract = factory.deploy(args=[])

    web_data = contract.get_web_data(args=[]).call()
    prompt_result = contract.get_prompt_result(args=[]).call()

    assert "example" in str(web_data).lower()
    assert str(prompt_result).strip() == "OK"
