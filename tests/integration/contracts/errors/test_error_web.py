from gltest import get_contract_factory
from gltest.contracts.contract import Contract
from gltest.exceptions import DeploymentError
from tests.integration.contracts.errors.test_error_execution import (
    _deployment_error_to_tx_receipt,
    _check_result,
    _check_last_round,
)
import pytest

pytestmark = pytest.mark.error_handling


def test_web_system_error(setup_validators):
    setup_validators()
    factory = get_contract_factory("ErrorWebContract")
    try:
        factory.deploy(
            args=[1, "https://www.bbc.com/sport/football/scores-fixtures/2024-10-09"]
        )
    except DeploymentError as e:
        tx_receipt = _deployment_error_to_tx_receipt(e)
        _check_result(tx_receipt, "SystemError")


def test_web_connection_error(setup_validators):
    """Test web request when no internet connection"""
    setup_validators()
    factory = get_contract_factory("ErrorWebContract")
    try:
        factory.deploy(
            args=[2, "https://this-domain-definitely-does-not-exist-12345.com"]
        )
    except DeploymentError as e:
        tx_receipt = _deployment_error_to_tx_receipt(e)
        _check_result(tx_receipt, "WEBPAGE_LOAD_FAILED")


def test_private_ip(setup_validators):
    setup_validators()
    factory = get_contract_factory("ErrorWebContract")
    try:
        factory.deploy(args=[2, "http://10.255.255.1"])
    except DeploymentError as e:
        tx_receipt = _deployment_error_to_tx_receipt(e)
        _check_result(tx_receipt, "TLD_FORBIDDEN")


def test_web_timeout_error(setup_validators):
    setup_validators()
    factory = get_contract_factory("ErrorWebContract")
    try:
        factory.deploy(
            args=[2, "https://flash.siwalik.in/delay/300000/url/https://example.com"],
            wait_interval=20000,
            wait_retries=20,
        )
    except DeploymentError as e:
        tx_receipt = _deployment_error_to_tx_receipt(e)
        _check_last_round(tx_receipt, "Leader Timeout")


def test_web_404_error(setup_validators):
    """Test web request 404 error"""
    setup_validators()
    factory = get_contract_factory("ErrorWebContract")
    contract = factory.deploy(args=[2, "https://httpbin.org/status/404"])
    # No deployment error raised
    assert isinstance(contract, Contract)
