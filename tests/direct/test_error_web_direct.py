from pathlib import Path


def test_error_web_contract_direct_mode_success_path(
    direct_vm, direct_deploy, direct_alice
):
    """
    Direct-mode validation for ErrorWebContract, with nondet web mocked.

    This is intentionally not testing the simulator/network behavior; it's just
    a fast sanity check that the contract's nondet + eq_principle path runs.
    """
    contract_path = (
        Path(__file__).resolve().parent / "contracts" / "error_web_contract_direct.py"
    )

    direct_vm.sender = direct_alice
    url = "https://example.com/"
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

    # testcase=2 => eq_principle.strict_eq(get_url_data)
    direct_deploy(str(contract_path), 2, url)
