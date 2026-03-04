# rpc/endpoints.py
import random
import json
import time
import eth_utils
import logging
from functools import partial, wraps
from typing import Any
from backend.protocol_rpc.exceptions import JSONRPCError, NotFoundError
from sqlalchemy import Table
from sqlalchemy.orm import Session
import backend.validators as validators

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.llm_providers import LLMProviderRegistry
from backend.rollup.consensus_service import ConsensusService
from backend.database_handler.models import Base, TransactionStatus
from backend.domain.types import LLMProvider, Validator, TransactionType, SimConfig
from backend.node.create_nodes.providers import (
    get_default_provider_for,
    validate_provider,
)
from backend.protocol_rpc.message_handler.base import (
    IMessageHandler,
    get_client_session_id,
)
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.validators_registry import (
    ValidatorsRegistry,
    ModifiableValidatorsRegistry,
)

from backend.node.create_nodes.create_nodes import (
    random_validator_config,
)

from backend.protocol_rpc.transactions_parser import TransactionParser
from backend.errors.errors import InvalidAddressError, InvalidTransactionError

from backend.database_handler.transactions_processor import (
    TransactionAddressFilter,
    TransactionsProcessor,
)


logger = logging.getLogger(__name__)
from backend.node.base import Node, get_simulator_chain_id
from backend.node.types import ExecutionMode, ExecutionResultStatus
from backend.consensus.base import ConsensusAlgorithm
from backend.protocol_rpc.call_interceptor import handle_consensus_data_call

import base64
import hashlib
import os
import secrets as secrets_module
from backend.protocol_rpc.message_handler.types import LogEvent, EventType, EventScope
from backend.protocol_rpc.types import DecodedsubmitAppealDataArgs
from backend.database_handler.snapshot_manager import SnapshotManager
from backend.node.base import Manager as GenVMManager
import asyncio

# Limit concurrent GenVM executions on the jsonrpc path to prevent uvloop fd conflicts.
# Workers use asyncio.Semaphore(8) in consensus/base.py; gen_call had none, allowing
# unbounded concurrent GenVM socket operations that cause fd registry collisions.
_GENVM_CONCURRENCY = int(os.environ.get("GENVM_MAX_CONCURRENT", "8"))
_genvm_semaphore = asyncio.Semaphore(_GENVM_CONCURRENCY)

# ---------------------------------------------------------------------------
# Per-address rate limiting for gen_call / sim_call
# Prevents a single contract from monopolising all GenVM execution slots.
# ---------------------------------------------------------------------------
_RATE_LIMIT_WINDOW = float(
    os.environ.get("GEN_CALL_RATE_LIMIT_WINDOW", "10")
)  # seconds
_RATE_LIMIT_MAX = int(
    os.environ.get("GEN_CALL_RATE_LIMIT_MAX", "20")
)  # max requests per window per address

_address_request_log: dict[str, list[float]] = {}  # {address: [timestamp, ...]}

_rate_limit_logger = logging.getLogger(__name__ + ".rate_limit")


def _check_rate_limit(address: str) -> None:
    """Reject if address exceeds rate limit. Prunes old entries."""
    now = time.monotonic()
    timestamps = _address_request_log.get(address, [])
    cutoff = now - _RATE_LIMIT_WINDOW
    timestamps = [t for t in timestamps if t > cutoff]
    if len(timestamps) >= _RATE_LIMIT_MAX:
        _rate_limit_logger.warning(
            f"Rate limit exceeded for {address}: {len(timestamps)} requests in {_RATE_LIMIT_WINDOW}s window"
        )
        raise JSONRPCError(
            code=-32005,
            message=f"Rate limit exceeded: max {_RATE_LIMIT_MAX} gen_call requests per {_RATE_LIMIT_WINDOW}s per contract address",
            data={"address": address, "retry_after_seconds": _RATE_LIMIT_WINDOW},
        )
    timestamps.append(now)
    _address_request_log[address] = timestamps


####### ADMIN ACCESS CONTROL #######
def require_admin_access(func):
    """
    Admin access control decorator:
    - ADMIN_API_KEY set → requires matching admin_key (works in all modes including hosted)
    - VITE_IS_HOSTED=true without ADMIN_API_KEY → blocked entirely
    - Neither set → open access (local dev)
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        is_hosted = os.getenv("VITE_IS_HOSTED") == "true"
        admin_api_key = os.getenv("ADMIN_API_KEY")

        # If admin key is configured, check it (works in all modes including hosted)
        if admin_api_key:
            request_key = kwargs.get("admin_key")
            if request_key == admin_api_key:
                # Valid admin key - proceed
                return func(*args, **kwargs)
            # Invalid key in any mode
            raise JSONRPCError(
                code=-32000,
                message="Invalid or missing admin key",
                data={},
            )

        # No admin key configured
        if is_hosted:
            # Hosted without admin key = blocked
            raise JSONRPCError(
                code=-32000,
                message="Operation not available in hosted mode",
                data={},
            )

        # Local dev = open access
        return func(*args, **kwargs)

    return wrapper


# Alias for backwards compatibility
check_forbidden_method_in_hosted_studio = require_admin_access


####### HELPER ENDPOINTS #######
def ping() -> str:
    return "OK"


####### SIMULATOR ENDPOINTS #######
@check_forbidden_method_in_hosted_studio
def clear_db_tables(session: Session, tables: list) -> None:
    for table_name in tables:
        table = Table(
            table_name, Base.metadata, autoload=True, autoload_with=session.bind
        )
        session.execute(table.delete())


def fund_account(
    session: Session,
    account_address: str,
    amount: int,
) -> str:
    """Fund an account within a request-scoped database session."""
    accounts_manager = AccountsManager(session)
    transactions_processor = TransactionsProcessor(session)

    if not accounts_manager.is_valid_address(account_address):
        raise InvalidAddressError(account_address)

    import secrets

    nonce = transactions_processor.get_transaction_count(None)
    transaction_hash = "0x" + secrets.token_hex(32)
    transactions_processor.insert_transaction(
        None, account_address, None, amount, 0, nonce, False, 0, None, transaction_hash
    )
    return transaction_hash


@check_forbidden_method_in_hosted_studio
def reset_defaults_llm_providers(llm_provider_registry: LLMProviderRegistry) -> None:
    llm_provider_registry.reset_defaults()


async def check_provider_is_available(
    genvm_manager: GenVMManager, provider: LLMProvider | dict
) -> bool:
    if isinstance(provider, LLMProvider):
        model = provider.model
        url = provider.plugin_config["api_url"]
        plugin = provider.plugin
        key = provider.plugin_config["api_key_env_var"]
        temperature = provider.config.get("temperature", 1)
        use_max_completion_tokens = provider.config.get(
            "use_max_completion_tokens", False
        )
    else:
        model = provider["model"]
        url = provider["plugin_config"]["api_url"]
        plugin = provider["plugin"]
        key = provider["plugin_config"]["api_key_env_var"]
        temperature = provider["config"].get("temperature", 1)
        use_max_completion_tokens = provider["config"].get(
            "use_max_completion_tokens", False
        )
    key = f"${{ENV[{key}]}}"
    res = await genvm_manager.try_llms(
        [
            {
                "host": url,
                "model": model,
                "provider": plugin,
                "key": key,
            }
        ],
        prompt={
            "system_message": "",
            "user_message": "respond with two letters 'ok' and nothing else. No quotes, no repetition",
            "temperature": temperature,
            "max_tokens": 500,
            "use_max_completion_tokens": use_max_completion_tokens,
            "images": [],
        },
    )
    if len(res) != 1:
        genvm_manager.logger.error(
            f"LLM provider check failed", provider=provider, result=res
        )
        return False
    res = res[0]
    if (text_response := res.get("response")) is None:
        genvm_manager.logger.error(
            f"LLM provider check failed", provider=provider, result=res
        )
        return False

    what_returned = text_response.strip().lower()
    if what_returned != "ok":
        genvm_manager.logger.error(
            f"LLM provider check failed", provider=provider, text_response=text_response
        )
        return False
    return True


async def get_providers_and_models(
    llm_provider_registry: LLMProviderRegistry,
    genvm_manager: GenVMManager,
) -> list[dict]:
    providers = await llm_provider_registry.get_all_dict()
    sem = asyncio.Semaphore(8)

    async def check_with_semaphore(genvm_manager, provider):
        async with sem:
            return await check_provider_is_available(genvm_manager, provider)

    availability = await asyncio.gather(
        *(check_with_semaphore(genvm_manager, p) for p in providers)
    )
    for provider, is_available in zip(providers, availability):
        provider["is_model_available"] = is_available
    return providers


@check_forbidden_method_in_hosted_studio
def add_provider(session: Session, params: dict) -> int:
    """Add a provider using the request-scoped session."""
    llm_provider_registry = LLMProviderRegistry(session)

    provider = LLMProvider(
        provider=params["provider"],
        model=params["model"],
        config=params["config"],
        plugin=params["plugin"],
        plugin_config=params["plugin_config"],
    )

    validate_provider(provider)

    return llm_provider_registry.add(provider)


@check_forbidden_method_in_hosted_studio
def update_provider(session: Session, id: int, params: dict) -> None:
    """Update a provider using the request-scoped session."""
    llm_provider_registry = LLMProviderRegistry(session)

    provider = LLMProvider(
        provider=params["provider"],
        model=params["model"],
        config=params["config"],
        plugin=params["plugin"],
        plugin_config=params["plugin_config"],
    )
    validate_provider(provider)

    llm_provider_registry.update(id, provider)


@check_forbidden_method_in_hosted_studio
def delete_provider(session: Session, id: int) -> None:
    """Delete a provider using the request-scoped session."""
    llm_provider_registry = LLMProviderRegistry(session)
    llm_provider_registry.delete(id)


async def create_validator(
    session: Session,
    validators_manager: validators.Manager,
    stake: int,
    provider: str,
    model: str,
    config: dict | None = None,
    plugin: str | None = None,
    plugin_config: dict | None = None,
) -> dict:
    # fallback for default provider
    llm_provider = None

    if config is None or plugin is None or plugin_config is None:
        llm_provider = get_default_provider_for(provider, model)
    else:
        llm_provider = LLMProvider(
            provider=provider,
            model=model,
            config=config,
            plugin=plugin,
            plugin_config=plugin_config,
        )
        validate_provider(llm_provider)

    accounts_manager = AccountsManager(session)

    account = accounts_manager.create_new_account()

    return await validators_manager.registry.create_validator(
        Validator(
            address=account.address,
            private_key=account.key,
            stake=stake,
            llmprovider=llm_provider,
        )
    )


@check_forbidden_method_in_hosted_studio
async def create_random_validator(
    session: Session,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    stake: int,
) -> dict:
    return (
        await create_random_validators(
            session,
            validators_manager,
            genvm_manager,
            1,
            stake,
            stake,
        )
    )[0]


@check_forbidden_method_in_hosted_studio
async def create_random_validators(
    session: Session,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    count: int,
    min_stake: int,
    max_stake: int,
    limit_providers: list[str] = None,
    limit_models: list[str] = None,
) -> list[dict]:
    accounts_manager = AccountsManager(session)
    llm_provider_registry = LLMProviderRegistry(session)

    limit_providers = limit_providers or []
    limit_models = limit_models or []

    details = await random_validator_config(
        llm_provider_registry.get_all,
        partial(check_provider_is_available, genvm_manager),
        set(limit_providers),
        set(limit_models),
        count,
    )

    response = []
    for detail in details:
        stake = random.randint(min_stake, max_stake)
        validator_account = accounts_manager.create_new_account()

        validator = await validators_manager.registry.create_validator(
            Validator(
                address=validator_account.address,
                private_key=validator_account.key,
                stake=stake,
                llmprovider=detail,
            )
        )
        response.append(validator)

    return response


@check_forbidden_method_in_hosted_studio
async def update_validator(
    session: Session,
    validators_manager: validators.Manager,
    validator_address: str,
    stake: int,
    provider: str,
    model: str,
    config: dict | None = None,
    plugin: str | None = None,
    plugin_config: dict | None = None,
) -> dict:
    # Remove validation while adding migration to update the db address
    # if not accounts_manager.is_valid_address(validator_address):
    #     raise InvalidAddressError(validator_address)

    # fallback for default provider
    # TODO: only accept all or none of the config fields
    llm_provider = None
    if not (plugin and plugin_config):
        llm_provider = get_default_provider_for(provider, model)
        if config:
            llm_provider.config = config
    else:
        llm_provider = LLMProvider(
            provider=provider,
            model=model,
            config=config,
            plugin=plugin,
            plugin_config=plugin_config,
        )
        validate_provider(llm_provider)

    validator = Validator(
        address=validator_address,
        stake=stake,
        llmprovider=llm_provider,
    )
    return await validators_manager.registry.update_validator(validator)


@check_forbidden_method_in_hosted_studio
async def delete_validator(
    validators_manager: validators.Manager,
    validator_address: str,
) -> str:
    # Remove validation while adding migration to update the db address
    # if not accounts_manager.is_valid_address(validator_address):
    #     raise InvalidAddressError(validator_address)

    await validators_manager.registry.delete_validator(validator_address)
    return validator_address


@check_forbidden_method_in_hosted_studio
async def delete_all_validators(
    validators_manager: validators.Manager,
) -> list:
    await validators_manager.registry.delete_all_validators()
    return validators_manager.registry.get_all_validators()


def get_all_validators(validators_registry: ValidatorsRegistry) -> list:
    return validators_registry.get_all_validators(include_private_key=False)


def get_validator(
    validators_registry: ValidatorsRegistry, validator_address: str
) -> dict:
    return validators_registry.get_validator(
        validator_address=validator_address, include_private_key=False
    )


def count_validators(validators_registry: ValidatorsRegistry) -> int:
    return validators_registry.count_validators()


####### GEN ENDPOINTS #######
async def get_contract_schema(
    accounts_manager: AccountsManager,
    msg_handler: MessageHandler,
    contract_address: str,
) -> dict:
    contract_snapshot = ContractSnapshot(contract_address, session)
    code_b64 = contract_snapshot.extract_deployed_code_b64()
    if not code_b64:
        raise InvalidAddressError(
            contract_address,
            "Contract not deployed.",
        )

    node = Node(  # Mock node just to get the data from the GenVM
        contract_snapshot=None,
        validator_mode=ExecutionMode.LEADER,
        validator=Validator(
            address="",
            stake=0,
            llmprovider=LLMProvider(
                provider="",
                model="",
                config={},
                plugin="",
                plugin_config={},
            ),
        ),
        leader_receipt=None,
        msg_handler=msg_handler.with_client_session(get_client_session_id()),
        contract_snapshot_factory=None,
        manager=genvm_manager,
    )
    schema = await node.get_contract_schema(base64.b64decode(code_b64))
    return json.loads(schema)


async def get_contract_schema_for_code(
    genvm_manager: GenVMManager, msg_handler: IMessageHandler, contract_code_hex: str
) -> dict:
    node = Node(  # Mock node just to get the data from the GenVM
        contract_snapshot=None,
        validator_mode=ExecutionMode.LEADER,
        validator=Validator(
            address="",
            stake=0,
            llmprovider=LLMProvider(
                provider="",
                model="",
                config={},
                plugin="",
                plugin_config={},
            ),
        ),
        leader_receipt=None,
        msg_handler=msg_handler.with_client_session(get_client_session_id()),
        contract_snapshot_factory=None,
        manager=genvm_manager,
    )
    # Contract code is expected to be a hex string, but it can be a plain UTF-8 string
    # When hex decoding fails, fall back to UTF-8 encoding
    try:
        contract_code = eth_utils.hexadecimal.decode_hex(contract_code_hex)
    except ValueError:
        logger.debug(
            "Contract code is not hex-encoded, treating as UTF-8 string",
        )
        contract_code = contract_code_hex.encode("utf-8")
    schema = await node.get_contract_schema(contract_code)
    return json.loads(schema)


def get_contract_code(session: Session, contract_address: str) -> str:
    contract_snapshot = ContractSnapshot(contract_address, session)
    code_b64 = contract_snapshot.extract_deployed_code_b64()
    if not code_b64:
        raise InvalidAddressError(
            contract_address,
            "Contract not deployed",
        )
    return code_b64


async def _execute_call_with_snapshot(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    params: dict,
):
    """Common logic for gen_call and sim_call"""
    sim_config_obj = None
    if "sim_config" in params and params["sim_config"]:
        sim_config_obj = SimConfig.from_dict(params["sim_config"])

    virtual_validators = []

    # Use sim_config_obj if provided
    if sim_config_obj and sim_config_obj.validators:
        for validator in sim_config_obj.validators:
            provider = validator.provider
            model = validator.model
            config = validator.config
            plugin = validator.plugin
            plugin_config = validator.plugin_config
            try:
                if config is None or plugin is None or plugin_config is None:
                    llm_provider = get_default_provider_for(provider, model)
                else:
                    llm_provider = LLMProvider(
                        provider=provider,
                        model=model,
                        config=config,
                        plugin=plugin,
                        plugin_config=plugin_config,
                    )
                    validate_provider(llm_provider)
            except ValueError as e:
                raise JSONRPCError(code=-32602, message=str(e), data={}) from e
            account = accounts_manager.create_new_account()
            virtual_validators.append(
                Validator(
                    address=account.address,
                    private_key=account.key,
                    stake=validator.stake,
                    llmprovider=llm_provider,
                )
            )
    else:
        # Fallback to old behavior for backward compatibility
        sim_config = params.get("sim_config", {})
        provider = sim_config.get("provider")
        model = sim_config.get("model")

        if provider is not None and model is not None:
            config = sim_config.get("config")
            plugin = sim_config.get("plugin")
            plugin_config = sim_config.get("plugin_config")

            try:
                if config is None or plugin is None or plugin_config is None:
                    llm_provider = get_default_provider_for(provider, model)
                else:
                    llm_provider = LLMProvider(
                        provider=provider,
                        model=model,
                        config=config,
                        plugin=plugin,
                        plugin_config=plugin_config,
                    )
                    validate_provider(llm_provider)
            except ValueError as e:
                raise JSONRPCError(code=-32602, message=str(e), data={}) from e
            account = accounts_manager.create_new_account()
            virtual_validators.append(
                Validator(
                    address=account.address,
                    private_key=account.key,
                    stake=0,
                    llmprovider=llm_provider,
                )
            )
        elif provider is None and model is None:
            pass
        else:
            raise JSONRPCError(
                code=-32602,
                message="Both 'provider' and 'model' must be supplied together.",
                data={},
            )

    if len(virtual_validators) > 0:
        snapshot_func = validators_manager.temporal_snapshot
        args = [virtual_validators]
    else:
        snapshot_func = validators_manager.snapshot
        args = []

    async with snapshot_func(*args) as snapshot:
        if len(snapshot.nodes) == 0:
            raise JSONRPCError("No validators exist to execute the call")

        receipt = await _gen_call_with_validator(
            session,
            accounts_manager,
            genvm_manager,
            msg_handler,
            transactions_parser,
            snapshot,
            params,
        )
        return receipt


async def gen_call(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    params: dict,
) -> str:
    receipt = await _execute_call_with_snapshot(
        session,
        accounts_manager,
        msg_handler,
        transactions_parser,
        validators_manager,
        genvm_manager,
        params,
    )
    return eth_utils.hexadecimal.encode_hex(receipt.result[1:])[2:]


def sim_lint_contract(source_code: str, filename: str = "contract.py") -> dict:
    """Lint GenVM contract source code.

    Args:
        source_code: Python source code to lint
        filename: Optional filename for error reporting

    Returns:
        dict with 'results' array and 'summary' object
    """
    from backend.protocol_rpc.contract_linter import ContractLinter

    linter = ContractLinter()
    return linter.lint_contract(source_code, filename)


async def sim_call(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    params: dict,
) -> dict:
    receipt = await _execute_call_with_snapshot(
        session,
        accounts_manager,
        msg_handler,
        transactions_parser,
        validators_manager,
        genvm_manager,
        params,
    )
    return receipt.to_dict()


async def _gen_call_with_validator(
    session: Session,
    accounts_manager: AccountsManager,
    genvm_manager: GenVMManager,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    validators_snapshot: validators.Snapshot,
    params: dict,
):
    type = params["type"]
    data = params["data"]
    to_address = params["to"]
    from_address = params["from"]
    transaction_hash_variant = (
        params["transaction_hash_variant"]
        if "transaction_hash_variant" in params
        else None
    )

    if not accounts_manager.is_valid_address(from_address):
        raise InvalidAddressError(from_address)

    if not accounts_manager.is_valid_address(to_address):
        raise InvalidAddressError(to_address)

    # Rate limit per contract address — reject early before acquiring resources
    _check_rate_limit(to_address)

    if transaction_hash_variant == "latest-final":
        state_status = "finalized"
    else:
        state_status = "accepted"

    # Get a validator
    if len(validators_snapshot.nodes) > 0:
        validator = validators_snapshot.nodes[0].validator
    else:
        raise JSONRPCError(f"No validators exist to execute the gen_call")

    # Create validator node
    node = Node(
        contract_snapshot=ContractSnapshot(to_address, session),
        contract_snapshot_factory=partial(ContractSnapshot, session=session),
        validator_mode=ExecutionMode.LEADER,
        validator=validator,
        leader_receipt=None,
        msg_handler=msg_handler.with_client_session(get_client_session_id()),
        validators_snapshot=validators_snapshot,
        manager=genvm_manager,
    )

    sc_raw = params.get("sim_config")
    sim_config = SimConfig.from_dict(sc_raw) if sc_raw else None
    override_transaction_datetime: bool = (
        sim_config is not None and sim_config.genvm_datetime is not None
    )

    if _genvm_semaphore.locked():
        _rate_limit_logger.warning(
            f"GenVM at capacity ({_GENVM_CONCURRENCY} concurrent) — rejecting gen_call to {to_address}"
        )
        raise JSONRPCError(
            code=-32006,
            message=f"Server busy: all {_GENVM_CONCURRENCY} execution slots occupied, retry later",
            data={"retry_after_seconds": 2},
        )

    async with _genvm_semaphore:
        if type == "read":
            # Pre-parse timestamp override and map errors
            txn_dt = None
            if sim_config and override_transaction_datetime:
                try:
                    txn_dt = sim_config.genvm_datetime_as_datetime
                except ValueError as e:
                    raise JSONRPCError(
                        code=-32602,
                        message=f"Invalid sim_config.genvm_datetime: {sim_config.genvm_datetime}",
                        data={},
                    ) from e
            decoded_data = transactions_parser.decode_method_call_data(data)
            receipt = await node.get_contract_data(
                from_address=from_address,
                calldata=decoded_data.calldata,
                state_status=state_status,
                transaction_datetime=txn_dt,
            )
        elif type == "write":
            txn_created_at = None
            if sim_config and override_transaction_datetime:
                try:
                    _ = sim_config.genvm_datetime_as_datetime  # validation only
                    txn_created_at = sim_config.genvm_datetime
                except ValueError as e:
                    raise JSONRPCError(
                        code=-32602,
                        message=f"Invalid sim_config.genvm_datetime: {sim_config.genvm_datetime}",
                        data={},
                    ) from e
            decoded_data = transactions_parser.decode_method_send_data(data)
            receipt = await node.run_contract(
                from_address=from_address,
                calldata=decoded_data.calldata,
                transaction_created_at=txn_created_at,
            )
        elif type == "deploy":
            txn_created_at = None
            if sim_config and override_transaction_datetime:
                try:
                    _ = sim_config.genvm_datetime_as_datetime  # validation only
                    txn_created_at = sim_config.genvm_datetime
                except ValueError as e:
                    raise JSONRPCError(
                        code=-32602,
                        message=f"Invalid sim_config.genvm_datetime: {sim_config.genvm_datetime}",
                        data={},
                    ) from e
            decoded_data = transactions_parser.decode_deployment_data(data)
            receipt = await node.deploy_contract(
                from_address=from_address,
                code_to_deploy=decoded_data.contract_code,
                calldata=decoded_data.calldata,
                transaction_created_at=txn_created_at,
            )
        else:
            raise JSONRPCError(f"Invalid type: {type}")

    # Return the result of the write method
    if receipt.execution_result != ExecutionResultStatus.SUCCESS:
        raise JSONRPCError(
            message="running contract failed",
            data={"receipt": receipt.to_dict(), "params": params},
        )

    return receipt


####### ETH ENDPOINTS #######
def get_balance(
    accounts_manager: AccountsManager, account_address: str, block_tag: str = "latest"
) -> int:
    if not accounts_manager.is_valid_address(account_address):
        raise InvalidAddressError(
            account_address, f"Invalid address from_address: {account_address}"
        )
    account_balance = accounts_manager.get_account_balance(account_address)
    return account_balance


def get_transaction_count(
    transactions_processor: TransactionsProcessor, address: str, block: str = "latest"
) -> int:
    return transactions_processor.get_transaction_count(address)


def get_transaction_by_hash(
    transactions_processor: TransactionsProcessor,
    transaction_hash: str,
    sim_config: dict | None = None,
) -> dict:
    transaction = transactions_processor.get_transaction_by_hash(
        transaction_hash, sim_config
    )

    if transaction is None:
        raise NotFoundError(
            message=f"Transaction {transaction_hash} not found",
            data={"hash": transaction_hash},
        )
    return transaction


def get_studio_transaction_by_hash(
    transactions_processor: TransactionsProcessor,
    transaction_hash: str,
    full: bool = True,
) -> dict:
    transaction = transactions_processor.get_studio_transaction_by_hash(
        transaction_hash, full
    )

    if transaction is None:
        raise NotFoundError(
            message=f"Transaction {transaction_hash} not found",
            data={"hash": transaction_hash},
        )
    return transaction


def get_transaction_status(
    transactions_processor: TransactionsProcessor, transaction_hash: str
) -> str:
    status = transactions_processor.get_transaction_status(transaction_hash)
    if status is None:
        raise NotFoundError(
            message=f"Transaction {transaction_hash} not found",
            data={"hash": transaction_hash},
        )
    return status


async def eth_call(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    transactions_processor: TransactionsProcessor,
    params: dict,
    block_tag: str = "latest",
) -> str:
    to_address = params.get("to")
    from_address = params.get("from")
    data = params.get("data")

    if not to_address or not data:
        return "0x"

    # Validate to_address first
    if not accounts_manager.is_valid_address(to_address):
        raise InvalidAddressError(to_address)

    # Check if this is a ConsensusData contract call that we should handle locally
    # This should happen before early return to allow interception even without 'from'
    consensus_data_result = handle_consensus_data_call(
        transactions_processor, to_address, data
    )
    if consensus_data_result is not None:
        return consensus_data_result

    # Handle missing from_address after interceptor check
    if from_address is None:
        # Return '1' as a proper hex-encoded uint256
        return "0x0000000000000000000000000000000000000000000000000000000000000001"

    # Validate from_address if present
    if not accounts_manager.is_valid_address(from_address):
        raise InvalidAddressError(from_address)

    decoded_data = transactions_parser.decode_method_call_data(data)

    async with validators_manager.snapshot() as snapshot:
        print(snapshot.nodes)
        if len(snapshot.nodes) == 0:
            raise JSONRPCError(
                code=-32000,
                message="No validators available to execute eth_call",
                data={"reason": "no_validators"},
            )
        as_validator = snapshot.nodes[0].validator
        node = Node(  # Mock node just to get the data from the GenVM
            contract_snapshot=ContractSnapshot(to_address, session),
            contract_snapshot_factory=partial(ContractSnapshot, session=session),
            validator_mode=ExecutionMode.LEADER,
            validator=as_validator,
            leader_receipt=None,
            msg_handler=msg_handler.with_client_session(get_client_session_id()),
            validators_snapshot=snapshot,
            manager=genvm_manager,
        )

        receipt = await node.get_contract_data(
            from_address=as_validator.address,
            calldata=decoded_data.calldata,
        )

    if receipt.execution_result != ExecutionResultStatus.SUCCESS:
        raise JSONRPCError(
            message="running contract failed", data={"receipt": receipt.to_dict()}
        )
    return eth_utils.hexadecimal.encode_hex(receipt.result[1:])


def send_raw_transaction(
    session: Session,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    consensus_service: ConsensusService,
    signed_rollup_transaction: str,
    sim_config: dict | None = None,
) -> str:
    """Persist a raw transaction using a request-scoped session."""
    accounts_manager = AccountsManager(session)
    transactions_processor = TransactionsProcessor(session)

    # Decode transaction
    decoded_rollup_transaction = transactions_parser.decode_signed_transaction(
        signed_rollup_transaction
    )
    logger.debug("Decoded rollup transaction %s", decoded_rollup_transaction)

    # Validate transaction
    if decoded_rollup_transaction is None:
        raise InvalidTransactionError("Invalid transaction data")

    from_address = decoded_rollup_transaction.from_address
    value = decoded_rollup_transaction.value

    if not accounts_manager.is_valid_address(from_address):
        raise InvalidAddressError(
            from_address, f"Invalid address from_address: {from_address}"
        )

    transaction_signature_valid = transactions_parser.transaction_has_valid_signature(
        signed_rollup_transaction, decoded_rollup_transaction
    )
    if not transaction_signature_valid:
        raise InvalidTransactionError("Transaction signature verification failed")

    if isinstance(decoded_rollup_transaction.data, DecodedsubmitAppealDataArgs):
        tx_id = decoded_rollup_transaction.data.tx_id
        tx_id_hex = "0x" + tx_id.hex() if isinstance(tx_id, bytes) else tx_id
        transactions_processor.set_transaction_appeal(tx_id_hex, True)
        msg_handler.send_message(
            log_event=LogEvent(
                "transaction_appeal_updated",
                EventType.INFO,
                EventScope.CONSENSUS,
                "Set transaction appealed",
                {
                    "hash": tx_id_hex,
                },
            ),
            log_to_terminal=False,
        )
        return tx_id_hex
    else:
        transaction_hash = consensus_service.generate_transaction_hash(
            signed_rollup_transaction
        )
        to_address = decoded_rollup_transaction.to_address
        nonce = decoded_rollup_transaction.nonce
        value = decoded_rollup_transaction.value
        genlayer_transaction = transactions_parser.get_genlayer_transaction(
            decoded_rollup_transaction
        )

        transaction_data = {}
        leader_only = False
        execution_mode = "NORMAL"
        rollup_transaction_details = None
        if genlayer_transaction.type != TransactionType.SEND:
            leader_only = genlayer_transaction.data.leader_only
            execution_mode = genlayer_transaction.data.execution_mode
            rollup_transaction_details = consensus_service.add_transaction(
                signed_rollup_transaction, from_address
            )  # because hardhat accounts are not funded

            if (
                consensus_service.web3.is_connected()
                and rollup_transaction_details is None
            ):
                # raise JSONRPCError(
                #     code=-32000,
                #     message="Failed to add transaction to consensus layer",
                #     data={},
                # )
                logger.warning(
                    "Failed to add transaction to consensus layer",
                    extra={
                        "from_address": from_address,
                        "transaction_type": genlayer_transaction.type.name,
                        "leader_only": leader_only,
                    },
                )

        if genlayer_transaction.type == TransactionType.DEPLOY_CONTRACT:
            if value > 0:
                raise InvalidTransactionError("Deploy Transaction can't send value")

            if (
                rollup_transaction_details is None
                or not "recipient" in rollup_transaction_details
            ):
                new_account = accounts_manager.create_new_account()
                new_contract_address = new_account.address
            else:
                new_contract_address = rollup_transaction_details["recipient"]
                accounts_manager.create_new_account_with_address(new_contract_address)

            transaction_data = {
                "contract_address": new_contract_address,
                "contract_code": genlayer_transaction.data.contract_code,
                "calldata": genlayer_transaction.data.calldata,
            }
            to_address = new_contract_address
        elif genlayer_transaction.type == TransactionType.RUN_CONTRACT:
            # Contract Call
            to_address = genlayer_transaction.to_address
            if not accounts_manager.is_valid_address(to_address):
                raise InvalidAddressError(
                    to_address, f"Invalid address to_address: {to_address}"
                )

            if accounts_manager.get_account(to_address) is None:
                raise NotFoundError(
                    message="Contract not found",
                    data={"address": to_address},
                )

            transaction_data = {"calldata": genlayer_transaction.data.calldata}

        # Insert transaction into the database
        transactions_processor.insert_transaction(
            genlayer_transaction.from_address,
            to_address,
            transaction_data,
            value,
            genlayer_transaction.type.value,
            nonce,
            leader_only,
            genlayer_transaction.max_rotations,
            None,
            transaction_hash,
            genlayer_transaction.num_of_initial_validators,
            sim_config,
            None,  # triggered_on
            execution_mode,
        )

        # Post-insert verification: ensure the transaction is visible immediately
        try:
            verified_status = transactions_processor.get_transaction_status(
                transaction_hash
            )
            if verified_status is None:
                logger.error(
                    "Post-insert verification failed: transaction not found after commit",
                    extra={"hash": transaction_hash},
                )
                msg_handler.send_message(
                    log_event=LogEvent(
                        "transaction_post_insert_verification_failed",
                        EventType.ERROR,
                        EventScope.RPC,
                        "Inserted transaction not found immediately after commit",
                        {"hash": transaction_hash},
                    ),
                    log_to_terminal=False,
                )
        except Exception as e:
            logger.exception("Post-insert verification threw an exception")
            msg_handler.send_message(
                log_event=LogEvent(
                    "transaction_post_insert_verification_exception",
                    EventType.ERROR,
                    EventScope.RPC,
                    f"Exception during post-insert verification: {str(e)}",
                    {"hash": transaction_hash},
                ),
                log_to_terminal=False,
            )

        return transaction_hash


def get_transactions_for_address(
    transactions_processor: TransactionsProcessor,
    accounts_manager: AccountsManager,
    address: str,
    filter: str = TransactionAddressFilter.ALL.value,
) -> list[dict]:
    if not accounts_manager.is_valid_address(address):
        raise InvalidAddressError(address)

    return transactions_processor.get_transactions_for_address(
        address, TransactionAddressFilter(filter)
    )


@check_forbidden_method_in_hosted_studio
def set_finality_window_time(consensus: ConsensusAlgorithm, time: int) -> None:
    if consensus is None:
        # Silently ignore when consensus is not initialized
        return
    consensus.set_finality_window_time(time)


def get_finality_window_time(consensus: ConsensusAlgorithm) -> int:
    if consensus is None:
        # Return default finality window time when consensus is not initialized
        return os.environ.get("VITE_FINALITY_WINDOW", 1800)  # Default to 60 seconds
    return consensus.finality_window_time


def get_chain_id() -> str:
    return hex(get_simulator_chain_id())


def get_net_version() -> str:
    return str(get_simulator_chain_id())


def get_block_number(transactions_processor: TransactionsProcessor) -> str:
    transaction_count = transactions_processor.get_highest_timestamp()
    return hex(transaction_count)


def get_block_by_number(
    transactions_processor: TransactionsProcessor, block_number: str, full_tx: bool
) -> dict:
    block_number_int = 0

    if block_number == "latest":
        # Get latest block number using existing method
        block_number_int = int(get_block_number(transactions_processor), 16)
    else:
        try:
            block_number_int = int(block_number, 16)
        except ValueError:
            raise JSONRPCError(f"Invalid block number format: {block_number}")

    block_details = transactions_processor.get_transactions_for_block(
        block_number_int, include_full_tx=full_tx
    )

    if not block_details:
        raise NotFoundError(
            message="Block not found",
            data={"block_number": block_number},
        )

    return block_details


def get_gas_price() -> str:
    gas_price_in_wei = 0
    return hex(gas_price_in_wei)


def get_gas_estimate(data: Any) -> str:
    # Use zkSync Era's gas limit: 2^32 - 1 (4,294,967,295)
    gas_limit = 0xFFFFFFFF  # 4,294,967,295
    return hex(gas_limit)


def get_transaction_receipt(
    transactions_processor: TransactionsProcessor,
    transaction_hash: str,
) -> dict | None:

    transaction = transactions_processor.get_transaction_by_hash(transaction_hash)

    event_signature = "NewTransaction(bytes32,address,address)"
    event_signature_hash = eth_utils.keccak(text=event_signature).hex()

    to_addr = transaction.get("to_address")
    from_addr = transaction.get("from_address")

    logs = [
        {
            "address": to_addr,
            "topics": [
                f"0x{event_signature_hash}",
                transaction_hash,
                (
                    "0x000000000000000000000000" + to_addr.replace("0x", "")
                    if to_addr
                    else None
                ),
                (
                    "0x000000000000000000000000" + from_addr.replace("0x", "")
                    if from_addr
                    else None
                ),
            ],
            "data": "0x",
            "blockNumber": 0,
            "transactionHash": transaction_hash,
            "transactionIndex": 0,
            "blockHash": transaction_hash,
            "logIndex": 0,
            "removed": False,
        }
    ]

    receipt = {
        "transactionHash": transaction_hash,
        "transactionIndex": hex(0),
        "blockHash": transaction_hash,
        "blockNumber": hex(transaction.get("block_number", 0)),
        "from": from_addr,
        "to": to_addr,
        "cumulativeGasUsed": hex(transaction.get("gas_used", 8000000)),
        "gasUsed": hex(transaction.get("gas_used", 8000000)),
        "contractAddress": (
            transaction.get("contract_address")
            if transaction.get("contract_address")
            else None
        ),
        "logs": logs,
        "logsBloom": "0x" + "00" * 256,
        "status": hex(1 if transaction.get("status", True) else 0),
    }

    return receipt


def get_block_by_hash(
    transactions_processor: TransactionsProcessor,
    block_hash: str,
    full_tx: bool = False,
) -> dict | None:

    transaction = transactions_processor.get_transaction_by_hash(block_hash)

    if not transaction:
        return None

    block_details = {
        "hash": block_hash,
        "parentHash": "0x" + "00" * 32,
        "number": hex(transaction.get("block_number", 0)),
        "timestamp": hex(transaction.get("timestamp", 0)),
        "nonce": "0x" + "00" * 8,
        "transactionsRoot": "0x" + "00" * 32,
        "stateRoot": "0x" + "00" * 32,
        "receiptsRoot": "0x" + "00" * 32,
        "miner": "0x" + "00" * 20,
        "difficulty": "0x1",
        "totalDifficulty": "0x1",
        "size": "0x0",
        "extraData": "0x",
        "gasLimit": hex(transaction.get("gas_limit", 8000000)),
        "gasUsed": hex(transaction.get("gas_used", 8000000)),
        "logsBloom": "0x" + "00" * 256,
        "transactions": [],
    }

    if full_tx:
        block_details["transactions"].append(transaction)
    else:
        block_details["transactions"].append(block_hash)

    return block_details


def get_contract(consensus_service: ConsensusService, contract_name: str) -> dict:
    """
    Get contract instance by name

    Args:
        consensus_service: The consensus service instance
        contract_name: Name of the contract to retrieve

    Returns:
        dict: Contract information including address and ABI
    """
    contract = consensus_service.load_contract(contract_name)

    if contract is None:
        raise NotFoundError(
            message=f"Contract {contract_name} not found",
            data={"contract_name": contract_name},
        )

    return {
        "address": contract["address"],
        "abi": contract["abi"],
        "bytecode": contract["bytecode"],
    }
