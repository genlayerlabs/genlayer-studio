import eth_utils
import requests
from eth_account import Account
from gltest import get_contract_factory
from gltest.utils import extract_contract_address

import backend.node.genvm.origin.calldata as calldata


def test_utf8_roundtrip_contract_over_rpc(setup_validators):
    setup_validators()

    factory = get_contract_factory(
        contract_file_path="tests/integration/icontracts/contracts/utf8_roundtrip_contract.py"
    )
    deploy_receipt = factory.deploy_contract_tx(args=[])
    contract_address = extract_contract_address(deploy_receipt)

    method_data = eth_utils.hexadecimal.encode_hex(
        calldata.encode({"method": "get_enriched_submission", "args": []})
    )
    raw_response = requests.post(
        "http://127.0.0.1:4000/api",
        json={
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {
                    "to": contract_address,
                    "from": Account.create().address,
                    "data": method_data,
                }
            ],
            "id": 1,
        },
        timeout=30,
    ).json()

    assert "error" not in raw_response, raw_response
    raw_hex = raw_response["result"]
    raw_bytes = eth_utils.hexadecimal.decode_hex(raw_hex)
    assert bytes.fromhex("c3a9") in raw_bytes

    decoded_from_raw = calldata.decode(raw_bytes)
    assert decoded_from_raw["analysis"][0]["analysis"] == "clichéd"

    gen_call_response = requests.post(
        "http://127.0.0.1:4000/api",
        json={
            "jsonrpc": "2.0",
            "method": "gen_call",
            "params": [
                {
                    "type": "read",
                    "from": Account.create().address,
                    "to": contract_address,
                    "data": method_data,
                    "transaction_hash_variant": "latest-nonfinal",
                }
            ],
            "id": 1,
        },
        timeout=30,
    ).json()

    assert "error" not in gen_call_response, gen_call_response
    gen_call_bytes = bytes.fromhex(gen_call_response["result"])
    assert bytes.fromhex("c3a9") in gen_call_bytes

    decoded_from_gen_call = calldata.decode(gen_call_bytes)
    assert decoded_from_gen_call["analysis"][0]["analysis"] == "clichéd"
