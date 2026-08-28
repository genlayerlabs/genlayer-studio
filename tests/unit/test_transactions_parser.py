import pytest
from unittest.mock import Mock, MagicMock
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.models import TransactionStatus
from backend.protocol_rpc.transactions_parser import (
    TransactionParser,
    DecodedMethodSendData,
    DecodedDeploymentData,
)
import re
from typing import Optional, List, Any
from rlp import encode
from web3 import Web3
import backend.node.genvm.origin.calldata as calldata
from backend.domain.types import TransactionType
from backend.rollup.default_contracts.consensus_main import (
    _abi_function_signature,
    get_default_consensus_main_contract,
)


@pytest.fixture
def transaction_parser():
    # Create a mock ConsensusService
    consensus_service = Mock()
    consensus_service.web3 = Mock()
    # Ensure no ABI is returned so function decoding is skipped
    consensus_service.load_contract = Mock(return_value=None)
    return TransactionParser(consensus_service)


def test_default_consensus_abi_exports_exact_decision_guarded_actions():
    abi = get_default_consensus_main_contract()["abi"]
    functions = {
        entry["name"]: entry for entry in abi if entry.get("type") == "function"
    }

    assert [item["type"] for item in functions["submitAppeal"]["inputs"]] == [
        "bytes32",
        "uint256",
    ]
    assert [item["type"] for item in functions["topUpAndSubmitAppeal"]["inputs"]] == [
        "bytes32",
        "uint256",
        "tuple",
    ]
    assert [item["type"] for item in functions["finalizeTransaction"]["inputs"]] == [
        "bytes32",
        "uint256",
    ]
    assert [item["type"] for item in functions["deploySalted"]["inputs"]] == ["tuple"]
    assert functions["deploySalted"]["stateMutability"] == "payable"
    # addTransaction is deliberately served at its pre-fee signature; the
    # fee-aware overload stays on the decode surface only. See
    # _SERVED_PRE_FEE_FUNCTION_NAMES.
    assert [item["type"] for item in functions["addTransaction"]["inputs"]] == [
        "address",
        "address",
        "uint256",
        "uint256",
        "bytes",
    ]
    assert [item["type"] for item in functions["topUpFees"]["inputs"]] == [
        "bytes32",
        "tuple",
    ]
    add_params = functions["deploySalted"]["inputs"][0]["components"]
    assert [item["type"] for item in add_params] == [
        "address",
        "address",
        "uint256",
        "uint256",
        "uint256",
        "uint256",
        "uint256",
        "tuple",
        "bytes",
        "tuple[]",
    ]
    assert [item["type"] for item in add_params[7]["components"]] == [
        "uint256",
        "uint256",
        "uint256",
        "uint256",
        "uint256",
        "uint256",
        "uint256[]",
        "uint256",
        "uint256",
        "uint256",
    ]
    assert [item["type"] for item in add_params[9]["components"]] == [
        "uint8",
        "bool",
        "uint256",
        "address",
        "bytes32",
        "uint256",
        "bytes",
    ]


def test_transaction_rpc_payload_stringifies_unsafe_fee_integers():
    payload = TransactionsProcessor._json_safe_numbers(
        {
            "fee_value": 1_100_000_000_001_000_000,
            "fee_accounting": {
                "primary_fee_budget": 1_100_000_000_001_000_000,
                "execution_budget_total": 1_000_000,
            },
        }
    )

    assert payload["fee_value"] == "1100000000001000000"
    assert payload["fee_accounting"]["primary_fee_budget"] == "1100000000001000000"
    assert payload["fee_accounting"]["execution_budget_total"] == 1_000_000


@pytest.mark.parametrize(
    "data, expected_result",
    [
        (
            [{"method": "__init__", "args": ["John Doe"]}, False],
            DecodedMethodSendData(
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
                execution_mode="NORMAL",
            ),
        ),
        (
            [{"method": "__init__", "args": ["John Doe"]}, True],
            DecodedMethodSendData(
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=True,
                execution_mode="LEADER_ONLY",
            ),
        ),
        (
            (
                [{"method": "__init__", "args": ["John Doe"]}]
            ),  # Should fallback to default
            DecodedMethodSendData(
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
                execution_mode="NORMAL",
            ),
        ),
    ],
)
def test_decode_method_send_data(transaction_parser, data, expected_result):
    encoded = encode([calldata.encode(data[0]), *data[1:]])
    assert transaction_parser.decode_method_send_data(encoded.hex()) == expected_result


@pytest.mark.parametrize(
    "data, expected_result",
    [
        (
            [
                b"class Test(name: str)",
                {"method": "__init__", "args": ["John Doe"]},
                False,
            ],
            DecodedDeploymentData(
                contract_code=b"class Test(name: str)",
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
                execution_mode="NORMAL",
            ),
        ),
        (
            [
                b"class Test(name: str)",
                {"method": "__init__", "args": ["John Doe"]},
                True,
            ],
            DecodedDeploymentData(
                contract_code=b"class Test(name: str)",
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=True,
                execution_mode="LEADER_ONLY",
            ),
        ),
        (
            [b"class Test(name: str)", {"method": "__init__", "args": ["John Doe"]}],
            DecodedDeploymentData(
                contract_code=b"class Test(name: str)",
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
                execution_mode="NORMAL",
            ),
        ),
    ],
)
def test_decode_deployment_data(transaction_parser, data, expected_result):
    encoded = encode([data[0], calldata.encode(data[1]), *data[2:]])
    assert transaction_parser.decode_deployment_data(encoded.hex()) == expected_result


@pytest.mark.parametrize(
    "tx_data, tx_result",
    [
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {
                    "leader_receipt": [
                        {
                            "result": {
                                "raw": "AKQYeyJyZWFzb25pbmciOiAiVGhlIGNvaW4gbXVzdCBub3QgYmUgZ2l2ZW4gdG8gYW55b25lLCByZ"
                                "WdhcmRsZXNzIG9mIHRoZSBjaXJjdW1zdGFuY2VzIG9yIHByb21pc2VzIG9mIGEgZGlmZmVyZW50IG91d"
                                "GNvbWUuIFRoZSBjb25zZXF1ZW5jZXMgb2YgZ2l2aW5nIHRoZSBjb2luIGF3YXkgY291bGQgYmUgY2F0Y"
                                "XN0cm9waGljIGFuZCBpcnJldmVyc2libGUsIGV2ZW4gaWYgdGhlcmUgaXMgYSBwb3NzaWJpbGl0eSBvZ"
                                "iBhIHRpbWUgbG9vcCByZXNldHRpbmcgdGhlIHNpdHVhdGlvbi4gVGhlIGludGVncml0eSBvZiB0aGUgd"
                                "W5pdmVyc2UgYW5kIHRoZSBiYWxhbmNlIG9mIHBvd2VyIG11c3QgYmUgcHJlc2VydmVkIGJ5IGtlZXBpb"
                                "mcgdGhlIGNvaW4uIiwgImdpdmVfY29pbiI6IGZhbHNlfQ=="
                            }
                        }
                    ]
                },
            },
            '{"reasoning": "The coin must not be given to anyone, regardless of the circumstances or promises of a '
            "different outcome. The consequences of giving the coin away could be catastrophic and irreversible, "
            "even if there is a possibility of a time loop resetting the situation. The integrity of the universe "
            'and the balance of power must be preserved by keeping the coin.", "give_coin": false}',
        ),
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {"leader_receipt": [{"result": {"raw": "AAA="}}]},
            },
            "",
        ),
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {
                    "leader_receipt": [
                        {
                            "result": {
                                "raw": '```json\n{\n"transaction_success": true,\n"transaction_error": "",'
                                '\n"updated_balances": {"0x3bD9Cc00Fd6F9cAa866170b006a1182b760fC4D0": 100}\n}'
                                "\n```"
                            }
                        }
                    ]
                },
            },
            '```json\n{\n"transaction_success": true,\n"transaction_error": "",'
            '\n"updated_balances": {"0x3bD9Cc00Fd6F9cAa866170b006a1182b760fC4D0": 100}\n}'
            "\n```",
        ),
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {"leader_receipt": [{"result": "AAA="}]},
            },
            "",
        ),
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {"leader_receipt": [{"result": {}}]},
            },
            {},
        ),
    ],
)
def test_finalized_transaction_with_decoded_return_value(tx_data, tx_result):
    """
    verify return value is present at full transaction root and decoded
    """
    # Mock transaction
    mock_transaction_data = MagicMock()
    mock_transaction_data.hash = tx_data["hash"]
    mock_transaction_data.status = tx_data["status"]
    mock_transaction_data.consensus_data = tx_data["consensus_data"]
    get_full_tx = TransactionsProcessor._parse_transaction_data(mock_transaction_data)
    result = get_full_tx["result"]
    assert "result" in get_full_tx.keys()
    assert not isinstance(result, bytes)
    if isinstance(result, (bytes, str)):
        assert (
            bool(re.search(r"\\x[0-9a-fA-F]{2}", result)) is False
        )  # check byte string repr
    else:
        assert len(result) == 0
    assert result == tx_result


def _build_eip1559_raw(
    chain_id: int = 1,
    nonce: int = 0,
    max_priority_fee: int = 1,
    max_fee: int = 2,
    gas: int = 21000,
    to: bytes = b"",
    value: int = 0,
    data: bytes = b"",
    access_list: Optional[List] = None,
    v: int = 1,
    r: int = 1,
    s: int = 1,
) -> str:

    if access_list is None:
        access_list = []

    tx_fields = [
        chain_id,
        nonce,
        max_priority_fee,
        max_fee,
        gas,
        to,
        value,
        data,
        access_list,
        v,
        r,
        s,
    ]
    return "0x" + (b"\x02" + encode(tx_fields)).hex()


def _build_eip2930_raw(
    chain_id: int = 1,
    nonce: int = 0,
    gas_price: int = 1,
    gas: int = 21000,
    to: bytes = b"",
    value: int = 0,
    data: bytes = b"",
    access_list: Optional[List[Any]] = None,
    v: int = 1,
    r: int = 1,
    s: int = 1,
) -> str:

    if access_list is None:
        access_list = []

    tx_fields = [
        chain_id,
        nonce,
        gas_price,
        gas,
        to,
        value,
        data,
        access_list,
        v,
        r,
        s,
    ]
    return "0x" + (b"\x01" + encode(tx_fields)).hex()


def test_decode_signed_transaction_typed_eip1559_minimal(
    transaction_parser, monkeypatch
):
    # Mock signature recovery to avoid needing a valid signature
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x1111111111111111111111111111111111111111",
    )

    raw = _build_eip1559_raw(
        chain_id=1,
        nonce=5,
        max_priority_fee=10,
        max_fee=20,
        gas=30000,
        to=b"",  # contract creation / burn address
        value=1000,
        data=b"",
    )

    decoded = transaction_parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.type == 2
    assert decoded.from_address == "0x1111111111111111111111111111111111111111"
    assert decoded.to_address is None
    assert decoded.nonce == 5
    assert decoded.value == 1000
    # No ABI provided, so data should be None
    assert decoded.data is None


def test_decode_signed_transaction_typed_eip2930_minimal(
    transaction_parser, monkeypatch
):
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x2222222222222222222222222222222222222222",
    )

    raw = _build_eip2930_raw(
        chain_id=1,
        nonce=7,
        gas_price=9,
        gas=25000,
        to=b"",  # contract creation / burn address
        value=2000,
        data=b"",
    )

    decoded = transaction_parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.type == 1
    assert decoded.from_address == "0x2222222222222222222222222222222222222222"
    assert decoded.to_address is None
    assert decoded.nonce == 7
    assert decoded.value == 2000
    assert decoded.data is None


def _fee_aware_call_data(parser, function_name: str, params: tuple) -> bytes:
    abi_entry = next(
        entry
        for entry in parser._get_contract_abi()
        if entry["type"] == "function"
        and entry["name"] == function_name
        and len(entry["inputs"]) == 1
    )
    input_type = parser._canonical_abi_type(abi_entry["inputs"][0])
    selector = parser.web3.keccak(text=f"{function_name}({input_type})")[:4]
    return selector + parser.web3.codec.encode([input_type], [params])


def _contract_call_data(
    parser,
    function_name: str,
    params: list,
    *,
    input_count: int | None = None,
) -> bytes:
    abi_entry = next(
        entry
        for entry in parser._get_contract_abi()
        if entry["type"] == "function"
        and entry["name"] == function_name
        and (input_count is None or len(entry["inputs"]) == input_count)
    )
    input_types = [
        parser._canonical_abi_type(abi_input) for abi_input in abi_entry["inputs"]
    ]
    selector = parser.web3.keccak(text=f"{function_name}({','.join(input_types)})")[:4]
    return selector + parser.web3.codec.encode(input_types, params)


@pytest.mark.parametrize("function_name", ["addTransaction", "deploySalted"])
def test_decode_signed_transaction_fee_aware_v06(function_name, monkeypatch):
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x3333333333333333333333333333333333333333",
    )

    consensus_service = Mock()
    consensus_service.web3 = Web3()
    consensus_service.load_contract = Mock(return_value={"abi": []})
    parser = TransactionParser(consensus_service)

    recipient = (
        "0x0000000000000000000000000000000000000000"
        if function_name == "deploySalted"
        else "0x4444444444444444444444444444444444444444"
    )
    tx_calldata = b"\xc3\x01"
    fees_distribution = (
        11,
        22,
        2,
        333,
        44,
        55,
        [3, 4],
        66,
        77,
        88,
    )
    message_allocations = [
        (
            1,
            True,
            2**256 - 1,
            "0x5555555555555555555555555555555555555555",
            b"\x00" * 32,
            99,
            b"\x12\x34",
        )
    ]
    params = (
        "0x3333333333333333333333333333333333333333",
        recipient,
        5,
        6,
        123456,
        9 if function_name == "deploySalted" else 0,
        12,
        fees_distribution,
        tx_calldata,
        message_allocations,
    )

    raw = _build_eip1559_raw(
        nonce=8,
        to=bytes.fromhex("0000000000000000000000000000000000000000"),
        value=70,
        data=_fee_aware_call_data(parser, function_name, params),
    )

    decoded = parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.value == 12
    assert decoded.fee_value == 58
    assert decoded.submitted_value == 70
    assert decoded.total_spend == 70
    assert decoded.data.function_name == function_name
    assert decoded.data.args.sender == "0x3333333333333333333333333333333333333333"
    assert decoded.data.args.recipient == Web3.to_checksum_address(recipient)
    assert decoded.data.args.num_of_initial_validators == 5
    assert decoded.data.args.max_rotations == 6
    assert decoded.data.args.valid_until == 123456
    assert decoded.data.args.salt_nonce == (9 if function_name == "deploySalted" else 0)
    assert decoded.data.args.user_value == 12
    assert decoded.data.args.data == tx_calldata
    assert decoded.data.args.message_allocations_count == 1
    assert decoded.data.args.message_allocations == [
        {
            "messageType": 1,
            "onAcceptance": True,
            "parentIndex": 2**256 - 1,
            "recipient": "0x5555555555555555555555555555555555555555",
            "callKey": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "budget": 99,
            "feeParams": b"\x12\x34",
        }
    ]
    assert decoded.data.args.fees_distribution == {
        "leaderTimeunitsAllocation": 11,
        "validatorTimeunitsAllocation": 22,
        "appealRounds": 2,
        "executionBudgetPerRound": 333,
        "executionConsumed": 44,
        "totalMessageFees": 55,
        "rotations": [3, 4],
        "maxPriceGenPerTimeUnit": 66,
        "storageFeeMaxGasPrice": 77,
        "receiptFeeMaxGasPrice": 88,
    }


@pytest.mark.parametrize(
    "function_name,top_up_and_submit",
    [
        ("topUpFees", False),
        ("topUpAndSubmitAppeal", True),
    ],
)
def test_decode_signed_transaction_fee_top_up_calls(
    function_name, top_up_and_submit, monkeypatch
):
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x3333333333333333333333333333333333333333",
    )

    consensus_service = Mock()
    consensus_service.web3 = Web3()
    consensus_service.load_contract = Mock(return_value={"abi": []})
    parser = TransactionParser(consensus_service)

    tx_id = b"\x12" * 32
    fees_distribution = (
        11,
        22,
        2,
        333,
        44,
        55,
        [3, 4, 5],
        66,
        77,
        88,
    )
    raw = _build_eip1559_raw(
        nonce=8,
        to=bytes.fromhex("0000000000000000000000000000000000000000"),
        value=1400,
        data=_contract_call_data(
            parser,
            function_name,
            [tx_id, fees_distribution],
            input_count=2,
        ),
    )

    decoded = parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.value == 0
    assert decoded.fee_value == 1400
    assert decoded.total_spend == 1400
    assert decoded.data.tx_id == tx_id
    assert decoded.data.fees_distribution == {
        "leaderTimeunitsAllocation": 11,
        "validatorTimeunitsAllocation": 22,
        "appealRounds": 2,
        "executionBudgetPerRound": 333,
        "executionConsumed": 44,
        "totalMessageFees": 55,
        "rotations": [3, 4, 5],
        "maxPriceGenPerTimeUnit": 66,
        "storageFeeMaxGasPrice": 77,
        "receiptFeeMaxGasPrice": 88,
    }
    assert getattr(decoded.data, "top_up_and_submit", False) is top_up_and_submit


def test_decode_signed_transaction_submit_appeal_uses_value_as_bond(monkeypatch):
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x3333333333333333333333333333333333333333",
    )

    consensus_service = Mock()
    consensus_service.web3 = Web3()
    consensus_service.load_contract = Mock(
        return_value={
            "abi": [
                {
                    "inputs": [
                        {"internalType": "bytes32", "name": "_txId", "type": "bytes32"}
                    ],
                    "name": "submitAppeal",
                    "outputs": [],
                    "stateMutability": "payable",
                    "type": "function",
                }
            ]
        }
    )
    parser = TransactionParser(consensus_service)

    tx_id = b"\x34" * 32
    raw = _build_eip1559_raw(
        nonce=9,
        to=bytes.fromhex("0000000000000000000000000000000000000000"),
        value=1400,
        data=_contract_call_data(parser, "submitAppeal", [tx_id]),
    )

    decoded = parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.value == 1400
    assert decoded.fee_value == 0
    assert decoded.total_spend == 1400
    assert decoded.data.tx_id == tx_id
    assert decoded.data.fees_distribution is None
    assert decoded.data.top_up_and_submit is False


def test_decode_latest_submit_appeal_preserves_expected_decision_id(monkeypatch):
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x3333333333333333333333333333333333333333",
    )
    consensus_service = Mock()
    consensus_service.web3 = Web3()
    consensus_service.load_contract = Mock(return_value={"abi": []})
    parser = TransactionParser(consensus_service)
    tx_id = b"\x56" * 32

    raw = _build_eip1559_raw(
        nonce=10,
        to=bytes.fromhex("0000000000000000000000000000000000000000"),
        value=1400,
        data=_contract_call_data(
            parser,
            "submitAppeal",
            [tx_id, 7],
            input_count=2,
        ),
    )

    decoded = parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.data.tx_id == tx_id
    assert decoded.data.expected_decision_id == 7
    assert decoded.data.top_up_and_submit is False


def test_decode_latest_top_up_and_submit_appeal_preserves_decision_id(monkeypatch):
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x3333333333333333333333333333333333333333",
    )
    consensus_service = Mock()
    consensus_service.web3 = Web3()
    consensus_service.load_contract = Mock(return_value={"abi": []})
    parser = TransactionParser(consensus_service)
    tx_id = b"\x78" * 32
    fees_distribution = (11, 22, 2, 333, 44, 55, [3, 4, 5], 66, 77, 88)

    raw = _build_eip1559_raw(
        nonce=11,
        to=bytes.fromhex("0000000000000000000000000000000000000000"),
        value=2800,
        data=_contract_call_data(
            parser,
            "topUpAndSubmitAppeal",
            [tx_id, 9, fees_distribution],
            input_count=3,
        ),
    )

    decoded = parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.fee_value == 2800
    assert decoded.data.expected_decision_id == 9
    assert decoded.data.top_up_and_submit is True
    assert decoded.data.fees_distribution["rotations"] == [3, 4, 5]


@pytest.mark.parametrize(
    ("arguments", "input_count", "expected_decision_id"),
    [
        ([b"\x9a" * 32, 17], 2, 17),
        ([b"\x9a" * 32], 1, None),
    ],
)
def test_decode_finalize_transaction_preserves_optional_decision_guard(
    monkeypatch, arguments, input_count, expected_decision_id
):
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x3333333333333333333333333333333333333333",
    )
    consensus_service = Mock()
    consensus_service.web3 = Web3()
    consensus_service.load_contract = Mock(return_value={"abi": []})
    parser = TransactionParser(consensus_service)

    raw = _build_eip1559_raw(
        nonce=12,
        to=bytes.fromhex("0000000000000000000000000000000000000000"),
        value=0,
        data=_contract_call_data(
            parser,
            "finalizeTransaction",
            arguments,
            input_count=input_count,
        ),
    )

    decoded = parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.data.tx_id == b"\x9a" * 32
    assert decoded.data.expected_decision_id == expected_decision_id


def _served_abi_functions() -> list:
    return [
        entry
        for entry in get_default_consensus_main_contract()["abi"]
        if entry.get("type") == "function"
    ]


def _decode_abi_signatures() -> set[str]:
    consensus_service = Mock()
    consensus_service.web3 = Web3()
    consensus_service.load_contract = Mock(
        return_value=get_default_consensus_main_contract()
    )
    parser = TransactionParser(consensus_service)
    return {
        _abi_function_signature(entry)
        for entry in parser._get_contract_abi()
        if entry.get("type") == "function"
    }


def test_decode_surface_keeps_pre_fee_entrypoints_alongside_v06():
    """Decoding must accept every generation of every entrypoint.

    This is the union TransactionParser matches selectors against. Losing the
    pre-fee addTransaction made every deploy and method call undecodable, so
    it was stored as a plain SEND.
    """
    signatures = _decode_abi_signatures()

    assert "addTransaction(address,address,uint256,uint256,bytes)" in signatures
    assert "submitAppeal(bytes32)" in signatures
    assert "finalizeTransaction(bytes32)" in signatures

    assert "submitAppeal(bytes32,uint256)" in signatures
    assert "finalizeTransaction(bytes32,uint256)" in signatures
    assert any(
        signature.startswith("addTransaction((") for signature in signatures
    ), signatures


def test_served_abi_function_names_are_unique():
    """The served ABI must be name-unique.

    Clients build a web3 contract from sim_getConsensusContract and resolve
    entrypoints with get_function_by_name(), which raises Web3ValueError as
    soon as a name is overloaded.
    """
    names = [entry["name"] for entry in _served_abi_functions()]

    assert len(names) == len(set(names)), sorted(
        name for name in names if names.count(name) > 1
    )


def test_served_abi_resolves_the_entrypoints_clients_look_up_by_name():
    """Mirrors genlayer_py._encode_add_transaction_data's exact lookup."""
    contract = Web3().eth.contract(abi=get_default_consensus_main_contract()["abi"])

    add_transaction = contract.get_function_by_name("addTransaction")
    assert add_transaction.signature == (
        "addTransaction(address,address,uint256,uint256,bytes)"
    )
    # Released and train genlayer-py both build five positional arguments and
    # encode them against argument_types; the fee-aware tuple form would make
    # that raise.
    assert len(add_transaction.argument_types) == 5

    # No Studio client encodes these from the served ABI, so they keep the
    # v0.6 decision-bound form the train genlayer-py requires.
    assert (
        contract.get_function_by_name("submitAppeal").signature
        == "submitAppeal(bytes32,uint256)"
    )
    assert (
        contract.get_function_by_name("finalizeTransaction").signature
        == "finalizeTransaction(bytes32,uint256)"
    )


def test_pre_fee_deploy_decodes_when_deployment_artifacts_are_missing(monkeypatch):
    """A pre-fee deploy must stay a DEPLOY_CONTRACT, never degrade to a SEND.

    ``ConsensusService.load_contract`` returns the default contract when
    ``hardhat/deployments/genlayer_network/ConsensusMain.json`` is absent. When
    the default ABI lost the pre-fee ``addTransaction`` overload, no selector
    matched, ``decode_signed_transaction`` produced ``data=None``, and
    ``get_genlayer_transaction`` fell back to a value transfer addressed to
    ConsensusMain — finalized immediately with no consensus_data.
    """
    sender = "0x715A17BA32a50bC11DADC257cb7c360FcaeE9dFA"
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: sender,
    )

    consensus_service = Mock()
    consensus_service.web3 = Web3()
    consensus_service.load_contract = Mock(
        return_value=get_default_consensus_main_contract()
    )
    parser = TransactionParser(consensus_service)

    contract_code = b"class Storage: pass"
    calldata = b"\xc3\x01"
    raw = _build_eip1559_raw(
        nonce=0,
        to=bytes.fromhex("b7278a61aa25c888815afc32ad3cc52ff24fe575"),
        value=0,
        data=_contract_call_data(
            parser,
            "addTransaction",
            [
                sender,
                "0x0000000000000000000000000000000000000000",
                5,
                3,
                encode([contract_code, calldata]),
            ],
            input_count=5,
        ),
    )

    decoded = parser.decode_signed_transaction(raw)
    assert decoded is not None
    assert decoded.data is not None

    genlayer_transaction = parser.get_genlayer_transaction(decoded)

    assert genlayer_transaction.type == TransactionType.DEPLOY_CONTRACT
    assert (
        genlayer_transaction.to_address == "0x0000000000000000000000000000000000000000"
    )
    assert genlayer_transaction.num_of_initial_validators == 5
    assert genlayer_transaction.data.contract_code == contract_code
    assert genlayer_transaction.data.calldata == calldata
