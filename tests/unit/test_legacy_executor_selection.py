"""Legacy contracts must work through ordinary RPCs, without sim_config."""

import base64
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from backend.consensus import base as consensus_base
from backend.consensus import worker as consensus_worker
from backend.consensus.base import (
    contract_snapshot_factory,
    transaction_genvm_executor_selector,
)
from backend.consensus.effects import RegisterContractEffect
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import (
    SimConfig,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from backend.node.base import Node
from backend.node.types import ExecutionResultStatus
from backend.node.genvm import base as genvm_base
from backend.node.genvm import get_code_slot
from backend.node.genvm.executor_selection import (
    legacy_executor_selector_for_child_code,
    legacy_executor_selector_for_code,
)
from backend.node.genvm.origin import calldata
from backend.node.genvm.origin.logger import NoLogger
from backend.protocol_rpc import endpoints

LEGACY_SELECTOR = r"re:^v0\.2\."
LEGACY_CODE = b"""# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *

class Legacy(gl.Contract):
    value: str

    def __init__(self, value: str):
        self.value = value

    @gl.public.view
    def get_value(self) -> str:
        return self.value
"""
ADDRESS = "0x" + "12" * 20
SCHEMA = {"methods": {"get_value": {"readonly": True}}}

V02_GENLAYER = "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6"
V01_GENLAYER = "py-genlayer:1j12s63yfjpva9ik2xgnffgrs6v44y1f52jvj9w7xvdn7qckd379"
V02_MULTI = "py-genlayer-multi:06zyvrlivjga0d5jlpdbprksc0pa6jmllxvp8s20hq1l512vh5yk"
V02_EMBEDDINGS = (
    "py-lib-genlayer-embeddings:0bmbm3cyfwxsyh454z53vxqjf47wz2q7smcqp1q4g4a6k2kidnyk"
)
V03_GENLAYER = "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0"
SHARED_LIB = "py-lib-protobuf:0riri6vk1ma3cnmlwls3d4771y0cf3c44afsl6gwfmnl2cpcbdl1"
# Split so the backend image's runner-pin grep does not require fake runners.
UNPINNED_GENLAYER = "py-" + "genlayer"


@pytest.fixture(autouse=True)
def no_installed_genvm(monkeypatch):
    monkeypatch.delenv("GENVMROOT", raising=False)


def _registry(ids):
    runners = {}
    for runner_id in ids:
        name, runner_hash = runner_id.split(":")
        runners.setdefault(name, []).append(runner_hash)
    return json.dumps(runners)


@pytest.fixture
def installed_genvm(tmp_path, monkeypatch):
    """Registries shaped like genvm-manager v0.6.0-rc9's executor lines."""
    lines = {
        "v0.2.17": [V02_GENLAYER, V01_GENLAYER, V02_MULTI, V02_EMBEDDINGS, SHARED_LIB],
        "v0.3.0-rc10": [V03_GENLAYER, SHARED_LIB],
    }
    for line, ids in lines.items():
        data = tmp_path / "executor" / line / "data"
        data.mkdir(parents=True)
        (data / "all.json").write_text(_registry(ids))
    monkeypatch.setenv("GENVMROOT", str(tmp_path))
    return tmp_path


def _zip_contract(runner_json: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("contract/__init__.py", "from genlayer import *\n")
        archive.writestr("runner.json", runner_json)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "header",
    [
        b"# v0.2.16",
        b"#v0.2.17",
        b"#\tv0.2.*\t",
        b"# v0.2.17-rc1\r",
        b"// v0.2.16",
        b"-- v0.2.16",
    ],
)
def test_legacy_version_header_matches_genvm_text_comment_rules(header):
    assert (
        legacy_executor_selector_for_code(header + b"\n# {}\ncode") == LEGACY_SELECTOR
    )


@pytest.mark.parametrize(
    "code",
    [
        b"",
        b"\xff\x00",
        b"# v0.3.0",
        b"# v1.2.16",
        b"# v0.20.16",
        b"# v0.2.16 not-a-version",
        b"# v0.2.16/../../",
        b"# v0.*.*",
        b" # v0.2.16",
        b"\n# v0.2.16",
        b"# {}\n# v0.2.16",
        b'print("# v0.2.16")',
    ],
)
def test_other_code_keeps_canonical_manager_selection(code):
    assert legacy_executor_selector_for_code(code) is None


RETAINED_ONLY_CODE = [
    # Rally's XID / BridgeSender.
    f'# v0.1.0\n# {{ "Depends": "{V01_GENLAYER}" }}\nclass C: pass\n'.encode(),
    # Rally's CampaignIC / CampaignShard: unversioned multi-line Seq.
    (
        "# {\n"
        '#   "Seq": [\n'
        f'#     {{ "Depends": "{V02_EMBEDDINGS}" }},\n'
        f'#     {{ "Depends": "{V02_GENLAYER}" }}\n'
        "#   ]\n"
        "# }\n"
        "import numpy\n"
    ).encode(),
    f'// {{ "Depends": "{V02_GENLAYER}" }}\ncode'.encode(),
    f'# v0.3.0\n# {{ "Depends": "{V02_GENLAYER}" }}\n'.encode(),
    # Rally's CampaignFactory: zip with runner.json.
    _zip_contract(f'{{\n  "Depends": "{V02_MULTI}"\n}}'),
]
RETAINED_ONLY_IDS = [
    "v0.1-header",
    "unversioned-seq",
    "slash-comment",
    "v0.3-header",
    "zip",
]
CAMPAIGN_CODE = RETAINED_ONLY_CODE[1]
CURRENT_CODE = f'# {{ "Depends": "{V03_GENLAYER}" }}\nclass C: pass\n'.encode()


@pytest.mark.parametrize("code", RETAINED_ONLY_CODE, ids=RETAINED_ONLY_IDS)
def test_child_code_declaring_a_retained_only_runner_selects_legacy_executor(
    installed_genvm, code
):
    assert legacy_executor_selector_for_child_code(code) == LEGACY_SELECTOR


@pytest.mark.parametrize("code", RETAINED_ONLY_CODE, ids=RETAINED_ONLY_IDS)
def test_new_deployment_of_retained_only_runner_keeps_manager_selection(
    installed_genvm, code
):
    # Only contracts already on v0.2 may deploy onto it by runner.
    assert legacy_executor_selector_for_code(code) is None
    assert transaction_genvm_executor_selector(_deployment(code)) is None


def test_child_code_with_legacy_header_selects_legacy_executor():
    assert legacy_executor_selector_for_child_code(LEGACY_CODE) == LEGACY_SELECTOR


@pytest.mark.parametrize(
    "code",
    [
        f'# v0.3.0\n# {{ "Depends": "{V03_GENLAYER}" }}\n'.encode(),
        f'# {{ "Depends": "{V03_GENLAYER}" }}\n'.encode(),
        f'# v0.1.0\n# {{ "Depends": "{V03_GENLAYER}" }}\n'.encode(),
        # Present in both lines, so the newest line can load it.
        f'# {{ "Seq": [{{ "Depends": "{SHARED_LIB}" }}] }}\n'.encode(),
        f'# {{ "Depends": "{UNPINNED_GENLAYER}:latest" }}\n'.encode(),
        f'# {{ "Depends": "{UNPINNED_GENLAYER}:unknownhash" }}\n'.encode(),
        # GenVM reads only the leading comment block.
        f'# v0.3.0\nx = 1\n# {{ "Depends": "{V02_GENLAYER}" }}\n'.encode(),
        f'# {{ "Depends": "{V02_GENLAYER}"\n'.encode(),
        _zip_contract(f'{{ "Depends": "{V03_GENLAYER}" }}'),
        b"PK\x03\x04 not a zip",
    ],
    ids=[
        "v0.3-current",
        "unversioned-current",
        "v0.1-current",
        "shared-lib",
        "alias",
        "unknown-hash",
        "after-code",
        "malformed-json",
        "zip-current",
        "bad-zip",
    ],
)
def test_child_code_runnable_by_newest_line_keeps_manager_selection(
    installed_genvm, code
):
    assert legacy_executor_selector_for_child_code(code) is None


def test_retained_runner_without_installed_registries_keeps_manager_selection():
    code = f'# {{ "Depends": "{V02_GENLAYER}" }}\n'.encode()
    assert legacy_executor_selector_for_child_code(code) is None


def _child_context(emitter_selector, *, parent=None, snapshot=True):
    parent = parent or Transaction(
        hash="0xparent",
        status=TransactionStatus.PENDING,
        type=TransactionType.RUN_CONTRACT,
        from_address="0x" + "34" * 20,
        to_address=ADDRESS,
        data={},
        value=0,
    )
    return SimpleNamespace(
        transaction=parent,
        contract_snapshot=(
            SimpleNamespace(genvm_executor_selector=emitter_selector)
            if snapshot
            else None
        ),
    )


def _child_deploy(code):
    return [
        consensus_base.ZERO_ADDRESS,
        {"contract_address": consensus_base.ZERO_ADDRESS, "contract_code": code},
        TransactionType.DEPLOY_CONTRACT.value,
    ]


@pytest.mark.parametrize("emitter_selector", [LEGACY_SELECTOR, "v0.2.17"])
def test_v02_contract_deploying_retained_only_child_pins_it_to_v02(
    installed_genvm, emitter_selector
):
    # CampaignFactory.create_campaign deploying CampaignIC.
    sim_config = consensus_base._child_sim_config(
        _child_context(emitter_selector), _child_deploy(CAMPAIGN_CODE)
    )
    assert sim_config["genvm_executor_selector"] == LEGACY_SELECTOR
    assert sim_config["validators"] == []

    child = _deployment(CAMPAIGN_CODE)
    child.sim_config = SimConfig.from_dict(sim_config)
    assert transaction_genvm_executor_selector(child) == LEGACY_SELECTOR
    snapshot = contract_snapshot_factory(ADDRESS, None, child)
    assert snapshot.genvm_executor_selector == LEGACY_SELECTOR


def test_v02_child_deploying_from_its_constructor_pins_grandchild(installed_genvm):
    # CampaignIC.__init__ deploying its shards: the emitter is itself a deploy.
    parent = _deployment(CAMPAIGN_CODE, selector=LEGACY_SELECTOR)
    sim_config = consensus_base._child_sim_config(
        _child_context(None, parent=parent, snapshot=False),
        _child_deploy(CAMPAIGN_CODE),
    )
    assert sim_config["genvm_executor_selector"] == LEGACY_SELECTOR


@pytest.mark.parametrize(
    "emitter_selector, code",
    [
        (None, CAMPAIGN_CODE),
        ("v0.3.0-rc10", CAMPAIGN_CODE),
        (LEGACY_SELECTOR, CURRENT_CODE),
    ],
    ids=["current-emitter", "pinned-current-emitter", "current-child"],
)
def test_child_deployment_otherwise_keeps_manager_selection(
    installed_genvm, emitter_selector, code
):
    context = _child_context(emitter_selector)
    assert consensus_base._child_sim_config(context, _child_deploy(code)) is None


def test_child_call_and_explicit_pin_cascade_unchanged(installed_genvm):
    parent = _deployment(CAMPAIGN_CODE, selector="v0.2.17")
    context = _child_context(LEGACY_SELECTOR, parent=parent)
    call = [ADDRESS, {"calldata": b""}, TransactionType.RUN_CONTRACT.value]
    expected = parent.sim_config.to_dict()
    assert consensus_base._child_sim_config(context, call) == expected
    deploy = _child_deploy(CAMPAIGN_CODE)
    assert consensus_base._child_sim_config(context, deploy) == expected


def _deployment(code=LEGACY_CODE, selector=None):
    return Transaction(
        hash="0xabc",
        status=TransactionStatus.PENDING,
        type=TransactionType.DEPLOY_CONTRACT,
        from_address="0x" + "34" * 20,
        to_address=ADDRESS,
        data={"contract_code": base64.b64encode(code).decode("ascii")},
        value=0,
        sim_config=(
            SimConfig(validators=[], genvm_executor_selector=selector)
            if selector
            else None
        ),
    )


@pytest.fixture
def schema_run(monkeypatch):
    result = SimpleNamespace(
        result=genvm_base.ExecutionReturn(calldata.encode(json.dumps(SCHEMA))),
        genvm_log=[],
        stdout="",
        stderr="",
        processing_time=0,
    )
    run = AsyncMock(return_value=result)
    monkeypatch.setattr(genvm_base, "run_genvm_host", run)
    return run


def _node():
    node = Node.__new__(Node)
    node.manager = SimpleNamespace(url="http://unused")
    node.msg_handler = None
    node.logger = NoLogger()
    return node


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", ["true", "false"])
async def test_schema_for_legacy_code_selects_legacy_executor(
    schema_run, monkeypatch, debug
):
    monkeypatch.setenv("GENVM_DEBUG_MODE", debug)
    assert json.loads(await _node().get_contract_schema(LEGACY_CODE)) == SCHEMA
    kwargs = schema_run.await_args.kwargs
    assert kwargs.get("genvm_executor_selector") == LEGACY_SELECTOR
    assert kwargs["debug_mode"] == ("unsafe" if debug == "true" else "safe")
    proxy = schema_run.await_args.args[0].keywords["state_proxy"]
    assert bytes(proxy.data[get_code_slot(legacy=True)])[4:] == LEGACY_CODE
    # v0.2 interprets the v0.3 code slot as its locked-slot list. Writing code
    # there causes an OOM before Python starts, even with correct routing.
    assert get_code_slot() not in proxy.data


@pytest.mark.asyncio
async def test_schema_explicit_selector_takes_precedence(schema_run):
    await _node().get_contract_schema(LEGACY_CODE, genvm_executor_selector="v0.2.17")
    assert schema_run.await_args.kwargs["genvm_executor_selector"] == "v0.2.17"


@pytest.mark.asyncio
async def test_current_schema_keeps_default_executor(schema_run):
    await _node().get_contract_schema(LEGACY_CODE.replace(b"v0.2.16", b"v0.3.0"))
    assert schema_run.await_args.kwargs.get("genvm_executor_selector") is None
    proxy = schema_run.await_args.args[0].keywords["state_proxy"]
    assert get_code_slot() in proxy.data
    assert get_code_slot(legacy=True) not in proxy.data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code, explicit_selector, expected_selector",
    [
        (LEGACY_CODE, None, LEGACY_SELECTOR),
        (LEGACY_CODE, "v0.2.17", "v0.2.17"),
        (LEGACY_CODE, "v0.3.0-rc7", "v0.3.0-rc7"),
        (LEGACY_CODE.replace(b"v0.2.16", b"v0.3.0"), None, None),
    ],
)
async def test_simulated_deployment_selects_executor_before_execution(
    code, explicit_selector, expected_selector
):
    # gen_call / fee estimation create an empty snapshot without using the
    # consensus factory. Node must select the same line before executing it.
    node = _node()
    node.contract_snapshot = SimpleNamespace(genvm_executor_selector=explicit_selector)
    node._run_genvm = AsyncMock()
    await node.deploy_contract(ADDRESS, code, calldata.encode({"args": []}))
    node._run_genvm.assert_awaited_once()
    assert node.contract_snapshot.genvm_executor_selector == expected_selector


@pytest.mark.parametrize("selector", [LEGACY_SELECTOR, "v0.2.17"])
def test_deployed_legacy_code_uses_legacy_slot_not_locked_slots(selector):
    snapshot = ContractSnapshot.from_dict(
        {
            "contract_address": ADDRESS,
            "genvm_executor_selector": selector,
            "states": {
                "accepted": {
                    base64.b64encode(get_code_slot(legacy=True))
                    .decode(): base64.b64encode(
                        len(LEGACY_CODE).to_bytes(4, "little") + LEGACY_CODE
                    )
                    .decode(),
                    # A nonempty v0.2 lock list is not contract code.
                    base64.b64encode(get_code_slot())
                    .decode(): base64.b64encode((1).to_bytes(4, "little") + b"x" * 32)
                    .decode(),
                },
                "finalized": {},
            },
        }
    )
    assert base64.b64decode(snapshot.extract_deployed_code_b64()) == LEGACY_CODE


def test_legacy_deployment_infers_and_persists_selector_without_sim_config():
    transaction = _deployment()
    assert transaction.sim_config is None
    # Both the deploy snapshot and the accepted-contract effect use this helper.
    assert transaction_genvm_executor_selector(transaction) == LEGACY_SELECTOR
    snapshot = contract_snapshot_factory(ADDRESS, None, transaction)
    assert snapshot.genvm_executor_selector == LEGACY_SELECTOR


def test_explicit_deployment_selector_is_preserved():
    transaction = _deployment(selector="v0.2.17")
    assert transaction_genvm_executor_selector(transaction) == "v0.2.17"
    assert (
        contract_snapshot_factory(ADDRESS, None, transaction).genvm_executor_selector
        == "v0.2.17"
    )


def test_current_deployment_keeps_default_executor():
    transaction = _deployment(LEGACY_CODE.replace(b"v0.2.16", b"v0.3.0"))
    assert transaction_genvm_executor_selector(transaction) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [True, False])
async def test_accepted_deployment_registers_matching_code_slot_and_selector(
    monkeypatch, legacy
):
    code = LEGACY_CODE if legacy else LEGACY_CODE.replace(b"v0.2.16", b"v0.3.0")
    transaction = _deployment(code)
    transaction.data["contract_address"] = ADDRESS
    code_slot = base64.b64encode(get_code_slot(legacy=legacy)).decode()
    code_blob = base64.b64encode(len(code).to_bytes(4, "little") + code).decode()
    receipt = SimpleNamespace(
        execution_result=ExecutionResultStatus.SUCCESS,
        contract_state={code_slot: code_blob, "other-storage": "preserved"},
        node_config={},
    )
    context = SimpleNamespace(
        transaction=transaction,
        contract_snapshot=contract_snapshot_factory(ADDRESS, None, transaction),
        consensus_data=SimpleNamespace(
            leader_receipt=[receipt], to_dict=lambda **kwargs: {}
        ),
        validation_results=[],
    )
    for name in (
        "_apply_external_message_freeze_check",
        "_sync_reveal_message_fee_accounting",
        "_dispatch_messages_for_phase",
    ):
        monkeypatch.setattr(consensus_base, name, Mock())
    executor = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(consensus_base, "EffectExecutor", lambda _: executor)

    await consensus_base.AcceptedState().handle(context)

    pre_effects = executor.execute.await_args_list[0].args[0]
    registration = next(e for e in pre_effects if isinstance(e, RegisterContractEffect))
    stored = registration.contract_data
    assert stored["genvm_executor_selector"] == (LEGACY_SELECTOR if legacy else None)
    assert stored["data"]["state"]["accepted"] == receipt.contract_state
    assert stored["data"]["state"]["finalized"] == {code_slot: code_blob}


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [LEGACY_SELECTOR, "v0.2.17", None])
async def test_code_upgrade_preserves_executor_and_other_storage(monkeypatch, selector):
    legacy = selector is not None
    code_slot = base64.b64encode(get_code_slot(legacy=legacy)).decode()
    other_slot = base64.b64encode(get_code_slot(legacy=not legacy)).decode()
    # In v0.2, the modern code slot contains the lock list and must not change.
    contract = SimpleNamespace(
        genvm_executor_selector=selector,
        data={
            "state": {s: {other_slot: "preserved"} for s in ("accepted", "finalized")}
        },
    )
    session = MagicMock()
    query = (
        session.query.return_value.filter_by.return_value.with_for_update.return_value.populate_existing.return_value
    )
    query.one_or_none.return_value = contract
    tx = query.one.return_value
    tx.status = TransactionStatus.PENDING
    tx.worker_id = "test"
    tx.data = {}
    session.execute.return_value.first.return_value = None
    dispatch = AsyncMock()
    monkeypatch.setattr(
        consensus_worker.ConsensusAlgorithm,
        "dispatch_transaction_status_update",
        dispatch,
    )
    monkeypatch.setattr(consensus_worker, "TransactionsProcessor", Mock())
    worker = consensus_worker.ConsensusWorker.__new__(consensus_worker.ConsensusWorker)
    worker.worker_id = "test"
    worker.msg_handler = Mock()
    worker._generic_error_retries = {}
    code = LEGACY_CODE.decode()

    await worker._apply_upgrade_code(session, "0xabc", ADDRESS, code)

    assert tx.consensus_data["upgrade_result"] == "success"
    assert contract.genvm_executor_selector == selector
    for state in ("accepted", "finalized"):
        assert contract.data["state"][state][other_slot] == "preserved"
        assert (
            base64.b64decode(contract.data["state"][state][code_slot])[4:]
            == code.encode()
        )
    session.rollback.assert_not_called()
    assert dispatch.await_args.args[2] == TransactionStatus.FINALIZED


def test_non_deployment_does_not_infer_selector_from_transaction_data():
    transaction = _deployment()
    transaction.type = TransactionType.RUN_CONTRACT
    assert transaction_genvm_executor_selector(transaction) is None


@pytest.mark.parametrize("code_b64", [None, "contract code", "\u2603", 123])
def test_malformed_deployment_data_still_uses_normal_error_handling(code_b64):
    transaction = _deployment()
    transaction.data["contract_code"] = code_b64
    assert transaction_genvm_executor_selector(transaction) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_selector", ["v0.2.17", LEGACY_SELECTOR, None])
async def test_address_schema_preserves_stored_executor(monkeypatch, stored_selector):
    snapshot = Mock()
    snapshot.extract_deployed_code_b64.return_value = base64.b64encode(LEGACY_CODE)
    snapshot.genvm_executor_selector = stored_selector
    monkeypatch.setattr(endpoints, "ContractSnapshot", Mock(return_value=snapshot))
    node = Mock()
    node.get_contract_schema = AsyncMock(return_value=json.dumps(SCHEMA))
    monkeypatch.setattr(endpoints, "Node", Mock(return_value=node))
    msg_handler = Mock()
    msg_handler.with_client_session.return_value = msg_handler

    assert (
        await endpoints.get_contract_schema(None, Mock(), msg_handler, ADDRESS)
        == SCHEMA
    )
    node.get_contract_schema.assert_awaited_once_with(
        LEGACY_CODE, genvm_executor_selector=stored_selector
    )
