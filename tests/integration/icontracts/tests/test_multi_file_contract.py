from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded


def test_deploy(setup_validators):
    setup_validators()
    factory = get_contract_factory("MultiFileContract")
    contract = factory.deploy(args=[])

    wait_response = contract.wait(args=[])
    assert tx_execution_succeeded(wait_response)

    res = contract.test(args=[])
    assert res == "123"
