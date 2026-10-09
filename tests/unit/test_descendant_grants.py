from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_abi import encode
from eth_hash.auto import keccak

from backend.protocol_rpc.fees import (
    CONTRACT_DESCENDANT_GRANT_ABI_TYPE,
    CONTRACT_DESCENDANT_GRANT_DOMAIN,
    CONTRACT_DESCENDANT_GRANT_VERSION,
    EXTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE,
    INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE,
    NODE_ROOT_SENTINEL,
    AllocationDuplicateKey,
    AllocationTreeMalformed,
    AllocationTreeTooDeep,
    ArithmeticOverflow,
    ExternalAllocationInvalid,
    InvalidContractDescendantGrant,
    InvalidAppealRounds,
    InvalidFeeParams,
    PhaseTimeoutOutOfBounds,
    MessageAllocationsNotEqualBudget,
    MessageAllocationsRestricted,
    MessageBudgetExceeded,
    MessageDeclaredBudgetInsufficient,
    MessageNoMatchingAllocation,
    StudioFeePolicy,
    FEE_ACCOUNTING_KEY,
    _validate_contract_descendant_grant,
    consume_message_fees,
    cancel_fee_accounting,
    create_child_fee_accounting,
    decode_contract_descendant_grant,
    genvm_message_fee_allocation,
    fill_message_fee_payload_from_allocation,
    settle_fee_accounting,
    genvm_fee_context,
    uses_unmetered_message_fee_pool,
)
from backend.consensus.base import _attach_child_fee_accounting
from backend.node.genvm.origin.fees import UNMATCHED_EXTERNAL_GUARD_ALLOC


def _internal_fee_params(
    *,
    leader: int = 0,
    validator: int = 0,
    execution_budget: int = 10,
    rotations: list[int] | None = None,
    appeal_rounds: int | None = None,
    max_price: int = 1,
    storage_price: int = 1,
    receipt_price: int = 1,
) -> bytes:
    rotations = rotations or [0]
    return encode(
        [INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE],
        [
            (
                leader,
                validator,
                len(rotations) - 1 if appeal_rounds is None else appeal_rounds,
                execution_budget,
                rotations,
                max_price,
                storage_price,
                receipt_price,
            )
        ],
    )


def _node(
    *,
    message_type: int = 1,
    on_acceptance: bool = False,
    parent_index: int = NODE_ROOT_SENTINEL,
    recipient: str = "0x0000000000000000000000000000000000000011",
    call_key: bytes = bytes(32),
    budget: int = 30,
    fee_params: bytes | None = None,
) -> tuple:
    return (
        message_type,
        on_acceptance,
        parent_index,
        recipient,
        call_key,
        budget,
        fee_params or _internal_fee_params(),
    )


def _grant(policy: int, budget: int, nodes: list[tuple]) -> bytes:
    return encode(
        ["bytes32", CONTRACT_DESCENDANT_GRANT_ABI_TYPE],
        [CONTRACT_DESCENDANT_GRANT_DOMAIN, (1, policy, budget, nodes)],
    )


def _portable_vectors() -> list[tuple[str, bytes, int, str]]:
    vector_internal = _internal_fee_params(
        leader=5,
        validator=10,
        execution_budget=100_000_000_000_000_000,
        storage_price=1_000_000_000_000_000_000,
        receipt_price=1_000_000_000_000_000_000,
    )
    external = encode(
        [EXTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE],
        [(200_000, 1_000_000_000)],
    )
    single = [
        _node(
            budget=300_000_000_000_000_000,
            fee_params=vector_internal,
        )
    ]
    nested = [
        _node(
            budget=600_000_000_000_000_000,
            fee_params=vector_internal,
        ),
        _node(
            on_acceptance=True,
            parent_index=0,
            recipient="0x0000000000000000000000000000000000000022",
            budget=200_000_000_000_000_000,
            fee_params=vector_internal,
        ),
        _node(
            message_type=0,
            recipient="0x0000000000000000000000000000000000000033",
            call_key=bytes.fromhex(
                "b9ee2e14f00c68d60911d6c13db158044bd494f772f7785c2f108fdeb2949f81"
            ),
            budget=200_000_000_000_000,
            fee_params=external,
        ),
    ]
    return [
        (
            "closed-explicit",
            _grant(0, 0, []),
            224,
            "406f5f74fd9c19c37478c7833fb1cd5a5bf63e9fe49e7f969b52e01afc52573d",
        ),
        (
            "open-explicit",
            _grant(1, 300_000_000_000_000_000, []),
            224,
            "3bfb47b06c42438373ee95ee5b4818d37fd8042319ee42a46ef5fac6df0cef68",
        ),
        (
            "pinned-single-internal",
            _grant(2, 300_000_000_000_000_000, single),
            864,
            "07669715194cf1db8fbc1b70d6e4aa91fa9df21305a7eed1570c50112a74f47d",
        ),
        (
            "pinned-nested-internal-and-external-root",
            _grant(2, 600_200_000_000_000_000, nested),
            1_856,
            "cb946323c2548d70c2511cfcdd74564e0ebb7fa19610a2a379d9aebd4037882d",
        ),
        (
            "closed-legacy-omitted",
            b"",
            0,
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        ),
    ]


@pytest.mark.parametrize("name,payload,size,payload_hash", _portable_vectors())
def test_portable_descendant_grant_vectors(
    name: str,
    payload: bytes,
    size: int,
    payload_hash: str,
) -> None:
    grant = decode_contract_descendant_grant(payload)
    fixture = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/descendant_grant_vectors.json"
        ).read_text()
    )
    vector = next(item for item in fixture["validVectors"] if item["name"] == name)
    expected = vector["decodedGrant"]
    expected_allocations = [
        {
            "messageType": int(node["messageType"]),
            "onAcceptance": node["onAcceptance"],
            "parentIndex": int(node["parentIndex"]),
            "recipient": node["recipient"],
            "callKey": node["callKey"],
            "budget": int(node["budget"]),
            "feeParams": node["feeParams"],
        }
        for node in expected["allocations"]
    ]

    assert len(payload) == size, name
    assert keccak(payload).hex() == payload_hash, name
    assert int(expected["version"]) == CONTRACT_DESCENDANT_GRANT_VERSION
    assert (
        grant.policy.value
        == {"0": "closed", "1": "open", "2": "pinned"}[expected["policy"]]
    )
    assert grant.budget == int(expected["budget"])
    assert grant.allocations == expected_allocations


@pytest.mark.parametrize(
    "payload",
    [
        "0xnot-hex",
        encode(
            ["bytes32", CONTRACT_DESCENDANT_GRANT_ABI_TYPE],
            [bytes(32), (1, 0, 0, [])],
        ),
        encode(
            ["bytes32", CONTRACT_DESCENDANT_GRANT_ABI_TYPE],
            [CONTRACT_DESCENDANT_GRANT_DOMAIN, (2, 0, 0, [])],
        ),
        _grant(0, 0, []) + b"\x00",
        encode(
            ["bytes32", CONTRACT_DESCENDANT_GRANT_ABI_TYPE],
            [CONTRACT_DESCENDANT_GRANT_DOMAIN, (1, 3, 0, [])],
        ),
    ],
)
def test_portable_codec_negative_vectors(payload: bytes | str) -> None:
    with pytest.raises(InvalidContractDescendantGrant):
        decode_contract_descendant_grant(payload)


def test_pinned_root_sum_is_an_admission_error() -> None:
    node = _node()
    grant = decode_contract_descendant_grant(_grant(2, 31, [node]))

    with pytest.raises(MessageAllocationsNotEqualBudget):
        _validate_contract_descendant_grant(
            grant,
            child_fee_params={"appealRounds": 0},
            policy=StudioFeePolicy(max_allocation_tree_depth=24),
        )


def test_pinned_rejects_duplicate_sibling_keys() -> None:
    grant = decode_contract_descendant_grant(_grant(2, 60, [_node(), _node()]))

    with pytest.raises(AllocationDuplicateKey):
        _validate_contract_descendant_grant(
            grant,
            child_fee_params={"appealRounds": 0},
            policy=StudioFeePolicy(max_allocation_tree_depth=24),
        )


def test_pinned_rejects_forward_parent_and_excessive_depth() -> None:
    forward = decode_contract_descendant_grant(
        _grant(
            2,
            30,
            [
                _node(parent_index=1),
                _node(recipient="0x0000000000000000000000000000000000000022"),
            ],
        )
    )
    with pytest.raises(AllocationTreeMalformed):
        _validate_contract_descendant_grant(
            forward,
            child_fee_params={"appealRounds": 0},
            policy=StudioFeePolicy(max_allocation_tree_depth=24),
        )

    nested = decode_contract_descendant_grant(
        _grant(
            2,
            40,
            [
                _node(budget=40),
                _node(
                    parent_index=0,
                    recipient="0x0000000000000000000000000000000000000022",
                ),
            ],
        )
    )
    with pytest.raises(AllocationTreeTooDeep):
        _validate_contract_descendant_grant(
            nested,
            child_fee_params={"appealRounds": 0},
            policy=StudioFeePolicy(max_allocation_tree_depth=1),
        )


def test_pinned_rejects_noncanonical_fee_params_and_checked_overflow() -> None:
    noncanonical = decode_contract_descendant_grant(
        _grant(2, 30, [_node(fee_params=_internal_fee_params() + bytes(32))])
    )
    with pytest.raises(InvalidFeeParams):
        _validate_contract_descendant_grant(
            noncanonical,
            child_fee_params={"appealRounds": 0},
            policy=StudioFeePolicy(max_allocation_tree_depth=24),
        )

    overflow = decode_contract_descendant_grant(
        _grant(
            2,
            NODE_ROOT_SENTINEL,
            [
                _node(budget=NODE_ROOT_SENTINEL),
                _node(
                    parent_index=0,
                    recipient="0x0000000000000000000000000000000000000022",
                    budget=NODE_ROOT_SENTINEL,
                ),
                _node(
                    parent_index=0,
                    recipient="0x0000000000000000000000000000000000000033",
                    budget=NODE_ROOT_SENTINEL,
                ),
            ],
        )
    )
    with pytest.raises(ArithmeticOverflow):
        _validate_contract_descendant_grant(
            overflow,
            child_fee_params={"appealRounds": 0},
            policy=StudioFeePolicy(max_allocation_tree_depth=24),
        )


def _child_message(subtree: bytes, declared_budget: int) -> dict:
    return {
        "messageType": 1,
        "recipient": "0x0000000000000000000000000000000000000011",
        "value": 0,
        "onAcceptance": False,
        "feeParams": _internal_fee_params(),
        "declaredBudget": declared_budget,
        "allocationSubtree": "0x" + subtree.hex(),
        "callKey": "0x" + "00" * 32,
        "useBalance": True,
    }


def test_child_accounting_installs_closed_open_and_pinned_policies() -> None:
    omitted_fees, omitted = create_child_fee_accounting(
        message=_child_message(b"", 15),
        parent_fees_distribution=None,
    )
    explicit_fees, explicit = create_child_fee_accounting(
        message=_child_message(_grant(0, 0, []), 15),
        parent_fees_distribution=None,
    )
    open_fees, opened = create_child_fee_accounting(
        message=_child_message(_grant(1, 30, []), 45),
        parent_fees_distribution=None,
    )
    pinned_fees, pinned = create_child_fee_accounting(
        message=_child_message(_grant(2, 30, [_node()]), 40),
        parent_fees_distribution=None,
    )

    assert omitted_fees == explicit_fees
    assert omitted == explicit
    assert omitted["message_allocations_restricted"] is True
    assert explicit_fees["totalMessageFees"] == 0
    assert explicit["primary_fee_budget"] == 15
    assert explicit["message_allocation_policy"] == "closed"
    assert genvm_message_fee_allocation(omitted) == [UNMATCHED_EXTERNAL_GUARD_ALLOC]
    assert genvm_message_fee_allocation(explicit) == [UNMATCHED_EXTERNAL_GUARD_ALLOC]
    assert open_fees["totalMessageFees"] == 30
    assert opened["primary_fee_budget"] == 15
    assert opened["message_allocation_policy"] == "open"
    assert pinned_fees["totalMessageFees"] == 30
    assert pinned["message_allocation_policy"] == "pinned"
    assert len(pinned["message_allocations"]) == 1
    assert genvm_message_fee_allocation(pinned)[-1] == UNMATCHED_EXTERNAL_GUARD_ALLOC


def test_open_policy_materializes_bounded_wildcards_and_aggregate_cap() -> None:
    _, accounting = create_child_fee_accounting(
        message=_child_message(_grant(1, 30, []), 40),
        parent_fees_distribution=None,
    )

    allocations = genvm_message_fee_allocation(accounting)
    assert [node["on"] for node in allocations] == [
        "finalized",
        "finalized",
        "decided",
    ]
    assert all(node["recipient"] is None for node in allocations)
    assert all(node["budget"] == 30 for node in allocations)
    assert UNMATCHED_EXTERNAL_GUARD_ALLOC not in allocations

    message = _child_message(b"", 10)
    message["useBalance"] = False
    message["allocationSubtree"] = []
    updated = consume_message_fees(accounting, [message, message, message])
    assert updated["message_fee_consumed"] == 30
    with pytest.raises(MessageBudgetExceeded):
        consume_message_fees(updated, [message])


def test_closed_rejects_ordinary_descendants_but_allows_new_balance_branch() -> None:
    _, accounting = create_child_fee_accounting(
        message=_child_message(_grant(0, 0, []), 15),
        parent_fees_distribution=None,
    )
    ordinary = _child_message(b"", 10)
    ordinary["useBalance"] = False
    ordinary["allocationSubtree"] = []

    with pytest.raises(MessageAllocationsRestricted):
        consume_message_fees(accounting, [ordinary])

    nested_balance = _child_message(b"", 10)
    updated = consume_message_fees(accounting, [nested_balance])
    assert updated["message_fee_consumed"] == 0
    assert genvm_message_fee_allocation(accounting) == [UNMATCHED_EXTERNAL_GUARD_ALLOC]


def test_legacy_restricted_accounting_remains_closed_in_genvm_inputs() -> None:
    allocations = genvm_message_fee_allocation(
        {
            "message_fee_budget": 30,
            "message_allocations": [],
            "message_allocations_restricted": True,
        }
    )

    assert allocations == [UNMATCHED_EXTERNAL_GUARD_ALLOC]


@pytest.mark.parametrize("allocation_policy", ["closed", "pinned"])
def test_unmatched_external_hits_guard_before_consensus_restriction(
    allocation_policy: str,
) -> None:
    allocations = [_node()] if allocation_policy == "pinned" else []
    accounting = {
        "message_fee_budget": 30 if allocations else 0,
        "message_fee_consumed": 0,
        "message_allocations": [
            {
                "messageType": node[0],
                "onAcceptance": node[1],
                "parentIndex": node[2],
                "recipient": node[3],
                "callKey": "0x" + node[4].hex(),
                "budget": node[5],
                "feeParams": "0x" + node[6].hex(),
            }
            for node in allocations
        ],
        "message_allocation_policy": allocation_policy,
    }

    genvm_allocations = genvm_message_fee_allocation(accounting)
    assert genvm_allocations[-1] == UNMATCHED_EXTERNAL_GUARD_ALLOC
    assert [node for node in genvm_allocations if "External" in node["fee_params"]] == [
        UNMATCHED_EXTERNAL_GUARD_ALLOC
    ]
    assert genvm_allocations[-1]["budget"] < (
        genvm_allocations[-1]["fee_params"]["External"]["gas_limit"]
        * genvm_allocations[-1]["fee_params"]["External"]["max_gas_price"]
    )

    with pytest.raises(MessageNoMatchingAllocation):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 0,
                    "recipient": "0x0000000000000000000000000000000000000099",
                    "onAcceptance": False,
                    "declaredBudget": 0,
                    "callKey": "0x" + "12" * 32,
                    "gasUsed": 100,
                }
            ],
            policy=StudioFeePolicy(receipt_gas_price=7),
        )


def test_closed_external_on_acceptance_keeps_phase_error_precedence() -> None:
    accounting = {
        "message_fee_budget": 0,
        "message_fee_consumed": 0,
        "message_allocations": [],
        "message_allocation_policy": "closed",
    }

    with pytest.raises(ExternalAllocationInvalid, match="ExternalOnAcceptance"):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 0,
                    "recipient": "0x0000000000000000000000000000000000000099",
                    "onAcceptance": True,
                    "declaredBudget": 0,
                    "callKey": "0x" + "12" * 32,
                }
            ],
        )


@pytest.mark.parametrize("subtree", [b"", _grant(0, 0, [])])
@pytest.mark.parametrize(
    "fee_params,error",
    [
        (_internal_fee_params() + bytes(32), InvalidFeeParams),
        (_internal_fee_params(rotations=[0], appeal_rounds=1), InvalidAppealRounds),
        (_internal_fee_params(leader=1, validator=1), PhaseTimeoutOutOfBounds),
    ],
)
def test_omitted_and_explicit_closed_grants_validate_fee_terms(
    subtree: bytes, fee_params: bytes, error: type[Exception]
) -> None:
    message = _child_message(subtree, 1_000_000)
    message["feeParams"] = fee_params
    policy = StudioFeePolicy(
        min_propose_timeunits=2,
        min_commit_timeunits=2,
    )

    with pytest.raises(error):
        create_child_fee_accounting(
            message=message,
            parent_fees_distribution=None,
            policy=policy,
        )
    with pytest.raises(error):
        consume_message_fees(
            {"message_fee_budget": 0, "message_allocation_policy": "closed"},
            [message],
            policy=policy,
        )


def test_declared_budget_must_cover_primary_and_explicit_grant() -> None:
    message = _child_message(_grant(1, 30, []), 39)
    accounting = {
        "message_fee_budget": 0,
        "message_allocations": [],
        "message_allocation_policy": "open",
    }

    with pytest.raises(MessageDeclaredBudgetInsufficient):
        consume_message_fees(accounting, [message])


def _attach_descendant(parent: dict, message: dict) -> dict:
    parent["activation_prices_locked"] = True
    data = {}
    _attach_child_fee_accounting(
        SimpleNamespace(
            transaction=SimpleNamespace(
                origin_address="0x0000000000000000000000000000000000000099",
                from_address="0x0000000000000000000000000000000000000088",
                to_address="0x0000000000000000000000000000000000000077",
            )
        ),
        parent,
        fill_message_fee_payload_from_allocation(parent, message),
        SimpleNamespace(use_balance=message["useBalance"], value=0),
        data,
    )
    return data[FEE_ACCOUNTING_KEY]


@pytest.mark.parametrize("finish", [cancel_fee_accounting, settle_fee_accounting])
@pytest.mark.parametrize("policy", [1, 2])
def test_descendant_refund_returns_to_contract_depositor(finish, policy: int) -> None:
    depositor = "0x0000000000000000000000000000000000000066"
    _, parent = create_child_fee_accounting(
        message=_child_message(
            _grant(policy, 30, [_node()] if policy == 2 else []), 40
        ),
        parent_fees_distribution=None,
        sender=depositor,
    )
    message = _child_message(b"", 30)
    message["useBalance"] = False
    child = _attach_descendant(parent, message)

    settled, refund = finish(child)

    assert refund > 0
    assert {item["recipient"] for item in settled["fee_refund_settlements"]} == {
        depositor
    }
    assert sum(item["amount"] for item in settled["fee_refund_settlements"]) == refund
    assert finish(settled)[1] == 0


def test_pinned_descendants_keep_admitted_parent_appeal_rules() -> None:
    nodes = [
        _node(budget=50),
        _node(parent_index=0, budget=40),
        _node(
            parent_index=1,
            budget=30,
            on_acceptance=True,
            fee_params=_internal_fee_params(rotations=[0, 0]),
        ),
    ]
    _, parent = create_child_fee_accounting(
        message=_child_message(_grant(2, 50, nodes), 60),
        parent_fees_distribution=None,
        sender="0x0000000000000000000000000000000000000066",
    )

    for node, message_budget in zip(nodes, [40, 30, 0]):
        message = _child_message(b"", node[5])
        message.update(
            useBalance=False,
            feeParams=node[6],
            onAcceptance=node[1],
        )
        child = _attach_descendant(parent, message)
        assert child["message_allocations_from_grant"] is True
        assert child["message_fee_budget"] == message_budget
        parent = child


def test_zero_budget_balance_child_retains_closed_grant() -> None:
    message = _child_message(_grant(0, 0, []), 0)
    message["feeParams"] = _internal_fee_params(execution_budget=0)

    child = _attach_descendant({}, message)

    assert child["message_allocation_policy"] == "closed"
    assert child["message_allocations_from_grant"] is True
    assert child["sender"] == "0x0000000000000000000000000000000000000077"
    assert genvm_message_fee_allocation(child) == [UNMATCHED_EXTERNAL_GUARD_ALLOC]


@pytest.mark.parametrize("declared_budget", [0, 10])
def test_open_grant_descendant_with_no_remaining_budget_stays_metered(
    declared_budget: int,
) -> None:
    _, parent = create_child_fee_accounting(
        message=_child_message(_grant(1, 30, []), 40),
        parent_fees_distribution=None,
    )
    message = _child_message(b"", declared_budget)
    message["feeParams"] = _internal_fee_params(execution_budget=declared_budget)
    message["useBalance"] = False
    child = _attach_descendant(parent, message)

    assert child["message_fee_budget"] == 0
    assert not uses_unmetered_message_fee_pool(child)
    assert genvm_fee_context(child)[0]["message_fee"] == 0
    assert all(node["budget"] == 0 for node in genvm_message_fee_allocation(child))
    unfunded = {
        **message,
        "declaredBudget": 10,
        "feeParams": _internal_fee_params(),
    }
    with pytest.raises(MessageBudgetExceeded):
        consume_message_fees(child, [unfunded])
