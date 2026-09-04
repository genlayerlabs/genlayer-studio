import pytest
import eth_utils
import rlp

from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded

import backend.node.genvm.origin.calldata as calldata
from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status
from tests.integration.icontracts.conftest import setup_validators  # noqa: F401

pytestmark = pytest.mark.gasless

ZERO_ADDRESS = "0x" + "0" * 40
GASLESS_ENVS = (
    "GENLAYER_STUDIO_GEN_PER_TIME_UNIT",
    "GENLAYER_STUDIO_STORAGE_UNIT_PRICE",
    "GENLAYER_STUDIO_RECEIPT_GAS_PRICE",
)


@pytest.fixture(scope="module", autouse=True)
def _require_gasless_stack() -> dict:
    response = post_request_localhost(payload("sim_getFeeConfig"))
    try:
        body = response.json()
    except ValueError as exc:
        pytest.fail(
            "sim_getFeeConfig did not return JSON; gasless tests require "
            f"{', '.join(GASLESS_ENVS)} to be 0: {exc}"
        )

    if not has_success_status(body):
        pytest.fail(
            "sim_getFeeConfig failed; gasless tests require "
            f"{', '.join(GASLESS_ENVS)} to be 0: {body!r}"
        )

    result = body.get("result")
    if not isinstance(result, dict) or result.get("enabled") is not False:
        pytest.fail(
            "Gasless stack is not enabled; set "
            f"{', '.join(GASLESS_ENVS)} to 0. Fee config: {result!r}"
        )
    return result


def _sender_address(contract) -> str:
    account = getattr(contract, "account", None)
    address = getattr(account, "address", None)
    return address if isinstance(address, str) else ZERO_ADDRESS


def _estimate_params(contract, method: str, args: list) -> dict:
    # Write-call payloads are RLP-wrapped genvm calldata (see
    # TransactionParser.decode_method_send_data).
    encoded_data = eth_utils.hexadecimal.encode_hex(
        rlp.encode([calldata.encode({"method": method, "args": args}), False])
    )
    return {
        "scenarioName": method,
        "type": "write",
        "to": contract.address,
        "from": _sender_address(contract),
        "data": encoded_data,
        "value": "0x0",
        "transaction_hash_variant": "latest-nonfinal",
    }


def _assert_estimate_smoke_result(response: dict) -> dict:
    assert has_success_status(response), response
    result = response.get("result")
    assert isinstance(result, dict), response
    receipt = result.get("receipt")
    assert isinstance(receipt, dict), result
    execution_result = receipt.get("execution_result")
    if execution_result is not None:
        assert execution_result == "SUCCESS"
    assert isinstance(result.get("feeAccounting"), dict), result
    assert isinstance(result.get("feeReport"), dict), result
    return result


def test_fee_config_reports_gasless(_require_gasless_stack):
    fee_config = _require_gasless_stack

    assert fee_config["enabled"] is False
    assert fee_config["policy"]["genPerTimeUnit"] == "0"
    assert fee_config["policy"]["storageUnitPrice"] == "0"
    assert fee_config["policy"]["receiptGasPrice"] == "0"
    assert fee_config["defaultFees"]["feeValue"] == "0"


def test_deploy_write_read_without_fees(setup_validators, _require_gasless_stack):
    setup_validators()
    factory = get_contract_factory(contract_file_path="examples/contracts/storage.py")
    contract = factory.deploy(args=["a"])

    assert contract.get_storage(args=[]).call() == "a"

    transaction_response = contract.update_storage(args=["b"]).transact()
    assert tx_execution_succeeded(transaction_response)
    assert transaction_response.get("fees") is None

    tx_response = post_request_localhost(
        payload("eth_getTransactionByHash", transaction_response["hash"])
    ).json()
    assert has_success_status(tx_response), tx_response
    # Gasless contract: fee-less submissions carry no fee accounting; receipts
    # must report fees: null.
    assert tx_response["result"]["fees"] is None

    assert contract.get_storage(args=[]).call() == "b"

    # This is the RPC genlayer-js estimateTransactionFees hits.
    estimate_response = post_request_localhost(
        payload(
            "sim_estimateTransactionFees",
            _estimate_params(contract, "update_storage", ["c"]),
        )
    ).json()
    _assert_estimate_smoke_result(estimate_response)
    assert contract.get_storage(args=[]).call() == "b"

    default_fees = _require_gasless_stack["defaultFees"]
    with_fees_params = {
        **_estimate_params(contract, "update_storage", ["c"]),
        "fees": {
            "distribution": default_fees["distribution"],
            "feeValue": default_fees["feeValue"],
        },
    }
    with_fees_response = post_request_localhost(
        payload("sim_estimateTransactionFees", with_fees_params)
    ).json()
    # Tolerant behavior pin: explicit fee params are accepted and validated
    # against a zero-price policy where the required deposit is 0, rather than
    # rejected.
    assert has_success_status(with_fees_response), with_fees_response
    assert isinstance(with_fees_response.get("result"), dict), with_fees_response
