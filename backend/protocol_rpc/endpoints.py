# rpc/endpoints.py
import copy
import math
import random
import json
import time
import eth_utils
import logging
from contextlib import asynccontextmanager
from functools import partial, wraps
from typing import Any, Final, NoReturn, get_args
from backend.protocol_rpc.exceptions import (
    JSONRPCError,
    NotFoundError,
    QueueDepthExceeded,
)
from backend.protocol_rpc.contract_storage_quota import (
    enforce_contract_storage_quota,
    live_state_column_size,
)
from sqlalchemy import Table, text
from sqlalchemy.orm import Session
import backend.validators as validators

from backend.database_handler.contract_snapshot import (
    ContractSnapshot,
    fetch_deployed_code_b64,
)
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
)

from backend.node.create_nodes.create_nodes import (
    random_validator_config,
)

from backend.protocol_rpc.transactions_parser import TransactionParser
from backend.protocol_rpc.fees import (
    FEE_ACCOUNTING_KEY,
    VALIDATORS_PER_ROUND,
    FeeValidationError,
    StudioFeePolicy,
    acceptance_dispatch_pending,
    calculate_appeal_charge,
    calculate_round_fees,
    decode_internal_message_fee_params,
    create_fee_accounting,
    fee_accounting_with_discovered_messages,
    funding_policy_for_accounting,
    get_leader_rounds,
    normalize_fees_distribution,
    min_message_primary_fees,
    record_appeal_bond,
    record_execution_fee_consumption,
    required_fee_deposit,
    studio_fee_config,
    validate_transaction_fee_deposit,
)
from backend.protocol_rpc.ghost_factory import GhostFactoryConfig
from backend.consensus.history import (
    actual_leader_rotations_by_round,
    completed_consensus_round_index,
    current_decision_id as history_current_decision_id,
    has_terminal_validator_appeal,
    latest_decision_metadata,
    logical_fee_round_entries,
)
from backend.errors.errors import InvalidAddressError, InvalidTransactionError
from backend.database_handler.errors import ContractNotFoundError

from backend.database_handler.transactions_processor import (
    TRANSACTION_STATUS_CODES,
    TransactionAddressFilter,
    TransactionsProcessor,
)

logger = logging.getLogger(__name__)
TRANSACTION_NOT_FOUND_MESSAGE = "Transaction not found"
from backend.node.base import Node, _genvm_debug_mode, get_simulator_chain_id
from backend.node.genvm.base import is_valid_executor_selector
from backend.node.genvm.origin import base_host
from backend.node.types import ExecutionMode, ExecutionResultStatus
from backend.consensus.base import ConsensusAlgorithm
from backend.protocol_rpc.call_interceptor import handle_consensus_data_call

import base64
import hashlib
import os
import secrets as secrets_module
from backend.protocol_rpc.message_handler.types import LogEvent, EventType, EventScope
from backend.protocol_rpc.types import (
    DecodedRollupTransaction,
    DecodedRollupTransactionData,
    DecodedTopUpFeesDataArgs,
    DecodedFinalizeTransactionDataArgs,
    DecodedsubmitAppealDataArgs,
)
from backend.database_handler.snapshot_manager import SnapshotManager
from backend.node.base import Manager as GenVMManager
import asyncio

# Limit concurrent GenVM executions on the jsonrpc path to prevent uvloop fd
# conflicts and DB pool exhaustion while calls hold request-scoped sessions.
# Workers use CONSENSUS_VALIDATOR_MAX_CONCURRENT (default 8) in
# consensus/base.py; keep the RPC path bounded too.
_GENVM_CONCURRENCY = int(os.environ.get("GENVM_MAX_CONCURRENT", "8"))
_genvm_admission_semaphore = asyncio.Semaphore(_GENVM_CONCURRENCY)


class _EvmExecutionReverted(Exception):
    """A valid signed protocol envelope was mined but its call reverted."""

    def __init__(
        self,
        *,
        transaction_hash: str,
        from_address: str,
        to_address: str | None,
        nonce: int,
        reason: Exception,
    ) -> None:
        super().__init__(str(reason))
        self.transaction_hash = transaction_hash
        self.from_address = from_address
        self.to_address = to_address
        self.nonce = int(nonce)
        self.reason = reason


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
_gen_call_singleflight_logger = logging.getLogger(__name__ + ".singleflight")

_GEN_CALL_SINGLEFLIGHT_ENABLED = os.environ.get(
    "GEN_CALL_SINGLEFLIGHT_ENABLED", "true"
).lower() not in {"0", "false", "no", "off"}
_gen_call_singleflight_tasks: dict[str, asyncio.Task[str]] = {}
_gen_call_singleflight_lock = asyncio.Lock()


def _show_validator_private_keys_in_rpc() -> bool:
    return os.getenv("SHOW_VALIDATOR_PRIVATE_KEYS_IN_RPC", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_private_key_field(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.replace("_", "").replace("-", "").lower()
    return normalized == "privatekey" or normalized.endswith("privatekey")


def _sanitize_rpc_private_keys(value: Any) -> Any:
    """Return RPC data with private-key fields removed unless explicitly enabled."""
    if _show_validator_private_keys_in_rpc():
        return value

    if isinstance(value, dict):
        return {
            key: _sanitize_rpc_private_keys(item)
            for key, item in value.items()
            if not _is_private_key_field(key)
        }
    if isinstance(value, list):
        return [_sanitize_rpc_private_keys(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_rpc_private_keys(item) for item in value)
    return value


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
            message=f"Rate limit exceeded: max {_RATE_LIMIT_MAX} gen_call/sim_call requests per {_RATE_LIMIT_WINDOW}s per contract address",
            data={"address": address, "retry_after_seconds": _RATE_LIMIT_WINDOW},
        )
    timestamps.append(now)
    _address_request_log[address] = timestamps


@asynccontextmanager
async def _admit_genvm_call(method: str, to_address: str | None):
    """Reject GenVM-backed RPC calls instead of queueing unlimited work."""
    if _genvm_admission_semaphore.locked():
        _rate_limit_logger.warning(
            "GenVM at capacity (%s concurrent) - rejecting %s to %s",
            _GENVM_CONCURRENCY,
            method,
            to_address,
        )
        raise JSONRPCError(
            code=-32006,
            message=f"Server busy: all {_GENVM_CONCURRENCY} execution slots occupied, retry later",
            data={"retry_after_seconds": 2},
        )

    await _genvm_admission_semaphore.acquire()
    try:
        yield
    finally:
        _genvm_admission_semaphore.release()


def _gen_call_singleflight_key(params: dict) -> str | None:
    if not _GEN_CALL_SINGLEFLIGHT_ENABLED:
        return None
    if not isinstance(params, dict) or params.get("type") != "read":
        return None

    payload = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def _execute_gen_call_with_admission(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    params: dict,
) -> str:
    to_address = params.get("to") if isinstance(params, dict) else None
    async with _admit_genvm_call("gen_call", to_address):
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


async def _run_singleflight_gen_call(
    key: str,
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    params: dict,
) -> str:
    try:
        return await _execute_gen_call_with_admission(
            session,
            accounts_manager,
            msg_handler,
            transactions_parser,
            validators_manager,
            genvm_manager,
            params,
        )
    finally:
        task = asyncio.current_task()
        async with _gen_call_singleflight_lock:
            if _gen_call_singleflight_tasks.get(key) is task:
                _gen_call_singleflight_tasks.pop(key, None)


# ---------------------------------------------------------------------------
# Admission control on PENDING queue depth (eth_sendRawTransaction path).
#
# Consensus Queues initializes maxPendingTxsPerRecipient to 20. Studio uses the
# same per-recipient default; deployments may lower/raise it explicitly when
# they apply the matching governance setting on Consensus. The Studio-only
# sender cap remains optional.
# ---------------------------------------------------------------------------
def _parse_optional_positive_int(env_name: str) -> int | None:
    raw = os.environ.get(env_name)
    if raw is None or raw.strip() == "":
        return None
    try:
        parsed = int(raw)
    except (ValueError, TypeError):
        return None
    return parsed if parsed > 0 else None


_MAX_PENDING_PER_CONTRACT = (
    _parse_optional_positive_int("MAX_PENDING_PER_CONTRACT_DEFAULT") or 20
)
_MAX_PENDING_PER_SENDER = _parse_optional_positive_int("MAX_PENDING_PER_SENDER_DEFAULT")

# Generic guidance text in the error response — directs heavy users to
# non-shared deployments rather than retrying against the same instance.
_QUEUE_DEPTH_HELP = "The protocol limits each recipient's pending queue depth."


def _enforce_pending_queue_caps(
    transactions_processor,
    to_address: str | None,
    from_address: str | None,
) -> None:
    """Raise QueueDepthExceeded if the per-contract or per-sender cap is hit.

    Postgres admissions take deterministic advisory locks before counting so
    two concurrent submissions cannot both claim the final queue slot. This
    mirrors the serialized Queues insertion performed by Consensus.
    """
    session = transactions_processor.session
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        lock_keys = []
        if _MAX_PENDING_PER_CONTRACT is not None and to_address is not None:
            lock_keys.append(f"pending-recipient:{str(to_address).lower()}")
        if _MAX_PENDING_PER_SENDER is not None and from_address is not None:
            lock_keys.append(f"pending-sender:{str(from_address).lower()}")
        for lock_key in sorted(lock_keys):
            session.execute(
                text("SELECT pg_advisory_xact_lock(" "hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
    if _MAX_PENDING_PER_CONTRACT is not None and to_address is not None:
        contract_pending = session.execute(
            text(
                "SELECT COUNT(*) FROM transactions "
                "WHERE to_address = :addr AND status IN "
                "('PENDING', 'ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING')"
            ),
            {"addr": to_address},
        ).scalar()
        if (
            contract_pending is not None
            and contract_pending >= _MAX_PENDING_PER_CONTRACT
        ):
            raise QueueDepthExceeded(
                message=(
                    f"Contract {to_address} has {contract_pending} pending "
                    f"transactions (limit: {_MAX_PENDING_PER_CONTRACT}). "
                    f"{_QUEUE_DEPTH_HELP}"
                ),
                data={
                    "scope": "contract",
                    "address": to_address,
                    "pending": contract_pending,
                    "limit": _MAX_PENDING_PER_CONTRACT,
                },
            )

    if _MAX_PENDING_PER_SENDER is not None and from_address is not None:
        sender_pending = session.execute(
            text(
                "SELECT COUNT(*) FROM transactions "
                "WHERE from_address = :addr AND status = 'PENDING'"
            ),
            {"addr": from_address},
        ).scalar()
        if sender_pending is not None and sender_pending >= _MAX_PENDING_PER_SENDER:
            raise QueueDepthExceeded(
                message=(
                    f"Sender {from_address} has {sender_pending} pending "
                    f"transactions (limit: {_MAX_PENDING_PER_SENDER}). "
                    f"{_QUEUE_DEPTH_HELP}"
                ),
                data={
                    "scope": "sender",
                    "address": from_address,
                    "pending": sender_pending,
                    "limit": _MAX_PENDING_PER_SENDER,
                },
            )


def _allocate_top_level_ghost_address(
    transactions_processor: TransactionsProcessor,
    salt_nonce: int,
    namespace: str,
) -> str:
    """Reserve the next virtual GhostFactory address until admission commits."""

    transactions_processor.lock_ghost_factory()
    deployment_count = transactions_processor.get_successful_ghost_creation_count()
    address = GhostFactoryConfig.from_env().address_for(
        salt_nonce,
        deployment_count,
        namespace=namespace,
    )
    if transactions_processor.is_genvm_contract_address(address):
        # GhostFactory rejects a reused CREATE2 address and the enclosing
        # Consensus submission reverts.  The factory nonce is unchanged.
        raise InvalidTransactionError("GhostAlreadyDeployed")
    return address


def get_studio_fee_config() -> dict[str, Any]:
    return studio_fee_config(StudioFeePolicy.from_env())


def sim_calculate_round_fees(
    fees_distribution: dict[str, Any],
    num_of_validators: int = 5,
    round: int = 0,
) -> str:
    """Expose Studio's canonical fee quote for cross-stack conformance tests."""
    try:
        quote = calculate_round_fees(
            fees_distribution,
            int(num_of_validators),
            int(round),
            StudioFeePolicy.from_env(),
        )
    except FeeValidationError as exc:
        raise InvalidTransactionError(str(exc)) from exc
    return str(quote)


def sim_min_message_primary_fees(fee_params: str) -> str:
    """Quote the child primary-fee floor from canonical v0.6 ABI bytes."""
    try:
        decoded = decode_internal_message_fee_params(fee_params)
        quote = min_message_primary_fees(decoded, StudioFeePolicy.from_env())
    except FeeValidationError as exc:
        raise InvalidTransactionError(str(exc)) from exc
    return str(quote)


def sim_estimate_propose_receipt_gas(eq_outputs_length: int = 0) -> dict[str, str]:
    """Expose the deterministic v0.6 proposed-receipt metering formula."""
    policy = StudioFeePolicy.from_env()
    receipt_bytes = policy.estimate_propose_receipt_bytes(int(eq_outputs_length))
    gas = policy.estimate_propose_receipt_gas(receipt_bytes)
    return {
        "receiptBytes": str(receipt_bytes),
        "gas": str(gas),
        "fee": str(gas * policy.receipt_gas_price),
    }


def sim_estimate_message_reveal_gas(
    message_bytes: int,
    message_count: int,
) -> dict[str, str]:
    """Expose the deterministic v0.6 revealed-message write formula."""
    policy = StudioFeePolicy.from_env()
    gas = policy.estimate_consensus_message_reveal_gas(
        int(message_bytes),
        int(message_count),
    )
    return {
        "gas": str(gas),
        "fee": str(gas * policy.receipt_gas_price),
    }


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
    accounts_manager.credit_tx_value_once(transaction_hash, account_address, amount)
    return transaction_hash


@check_forbidden_method_in_hosted_studio
def reset_defaults_llm_providers(llm_provider_registry: LLMProviderRegistry) -> None:
    llm_provider_registry.reset_defaults()


async def check_provider_is_available(
    genvm_manager: GenVMManager, provider: LLMProvider | dict
) -> bool:
    try:
        if isinstance(provider, LLMProvider):
            model = provider.model
            url = provider.plugin_config["api_url"]
            plugin = provider.plugin
            key = provider.plugin_config["api_key_env_var"]
            config = provider.config or {}
        else:
            model = provider["model"]
            url = provider["plugin_config"]["api_url"]
            plugin = provider["plugin"]
            key = provider["plugin_config"]["api_key_env_var"]
            config = provider["config"] or {}
        temperature = config.get("temperature", 1)
        use_max_completion_tokens = config.get("use_max_completion_tokens", False)
        max_tokens = config.get("max_tokens", 500)
        known_config_keys = {"temperature", "max_tokens", "use_max_completion_tokens"}
        extra = {k: v for k, v in config.items() if k not in known_config_keys}
        prompt = {
            "system_message": "",
            "user_message": "respond with two letters 'ok' and nothing else. No quotes, no repetition",
            "temperature": temperature,
            "max_tokens": max_tokens,
            "use_max_completion_tokens": use_max_completion_tokens,
            "images": [],
        }
        if extra:
            prompt["extra"] = extra
        key = f"${{ENV[{key}]}}"
        timeout_s = float(
            os.environ.get("LLM_PROVIDER_AVAILABILITY_TIMEOUT_SECONDS", "20")
        )
        res = await asyncio.wait_for(
            genvm_manager.try_llms(
                [
                    {
                        "host": url,
                        "model": model,
                        "provider": plugin,
                        "key": key,
                    }
                ],
                prompt=prompt,
            ),
            timeout=timeout_s,
        )
    except Exception as exc:
        genvm_manager.logger.error(
            "LLM provider availability check failed",
            provider=provider,
            error=str(exc),
        )
        return False

    if len(res) != 1:
        genvm_manager.logger.error(
            "LLM provider check failed", provider=provider, result=res
        )
        return False
    res = res[0]
    if (text_response := res.get("response")) is None:
        genvm_manager.logger.error(
            "LLM provider check failed", provider=provider, result=res
        )
        return False

    what_returned = text_response.strip().lower()
    if what_returned != "ok":
        genvm_manager.logger.error(
            "LLM provider check failed", provider=provider, text_response=text_response
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


def get_contract_deployer(session: Session, contract_address: str) -> str | None:
    """Get the address that deployed a contract by looking up the deploy transaction."""
    from backend.database_handler.models import Transactions

    try:
        contract_address = eth_utils.to_checksum_address(contract_address)
    except Exception:
        pass
    deploy_tx = (
        session.query(Transactions)
        .filter(
            Transactions.to_address == contract_address,
            Transactions.type == TransactionType.DEPLOY_CONTRACT.value,
            Transactions.status == TransactionStatus.FINALIZED,
        )
        .first()
    )

    return deploy_tx.from_address if deploy_tx else None


def get_contract_nonce(session: Session, contract_address: str) -> int:
    """Get contract nonce (tx count TO this contract) for upgrade signatures."""
    from backend.database_handler.models import Transactions

    try:
        checksum_address = eth_utils.to_checksum_address(contract_address)
    except (ValueError, TypeError):
        checksum_address = contract_address

    count = (
        session.query(Transactions)
        .filter(Transactions.to_address == checksum_address)
        .count()
    )
    return count


def admin_upgrade_contract_code(
    session: Session,
    contract_address: str,
    new_code: str,
    signature: str | None = None,
    admin_key: str | None = None,
) -> dict:
    """
    Queue a contract code upgrade. Returns immediately with tx hash.
    Upgrade executes when worker processes it (after any pending txs for this contract).

    Access control:
    - Local (no env vars): open access
    - Hosted/Self-hosted: admin_key allows ANY contract, signature allows own contracts

    Args:
        session: Database session
        contract_address: Address of contract to upgrade
        new_code: New Python contract source code
        signature: Hex-encoded signature from deployer (required in hosted mode unless admin_key)
        admin_key: Admin API key for full access to any contract

    Returns:
        dict with transaction_hash for polling status
    """
    from backend.database_handler.models import (
        CurrentState,
        Transactions,
        TransactionStatus,
    )
    from backend.domain.types import TransactionType
    from eth_account.messages import encode_defunct
    from eth_account import Account
    from web3 import Web3
    import secrets

    # Normalize address to checksum format for consistent comparison
    try:
        contract_address = eth_utils.to_checksum_address(contract_address)
    except (ValueError, TypeError):
        pass  # Keep original if invalid - will fail later validation

    is_hosted = os.getenv("VITE_IS_HOSTED") == "true"
    admin_api_key = os.getenv("ADMIN_API_KEY")

    # Check if authorization is needed (hosted or self-hosted with key configured)
    needs_auth = is_hosted or admin_api_key

    if needs_auth:
        # Option 1: Admin key grants full access to ANY contract
        if admin_api_key and admin_key == admin_api_key:
            pass  # Authorized - proceed with upgrade

        # Option 2: Signature from deployer grants access to own contracts
        elif signature:
            try:
                tx_count = get_contract_nonce(session, contract_address)

                # Recover signer from signature
                new_code_hash = Web3.keccak(text=new_code)
                tx_count_bytes = tx_count.to_bytes(32, byteorder="big")
                message_hash = Web3.keccak(
                    Web3.to_bytes(hexstr=contract_address)
                    + tx_count_bytes
                    + new_code_hash
                )
                message = encode_defunct(primitive=message_hash)
                signer = Account.recover_message(message, signature=signature)

                # Verify signer is deployer
                deployer = get_contract_deployer(session, contract_address)
                if not deployer:
                    raise NotFoundError(
                        message="Contract not found",
                        data={"contract_address": contract_address},
                    )
                if signer.lower() != deployer.lower():
                    raise JSONRPCError(
                        code=-32000,
                        message="Only contract deployer can upgrade",
                        data={"signer": signer, "deployer": deployer},
                    )
            except JSONRPCError:
                raise
            except Exception as e:
                raise JSONRPCError(
                    code=-32000,
                    message=f"Invalid signature: {e!s}",
                    data={},
                ) from e

        else:
            raise JSONRPCError(
                code=-32000,
                message="Upgrade requires admin key or deployer signature",
                data={},
            )

    # Validate new code is not empty
    if not new_code or not new_code.strip():
        raise JSONRPCError(
            code=-32602,
            message="Contract code cannot be empty",
            data={},
        )

    # Validate contract exists and is deployed
    contract = session.query(CurrentState).filter_by(id=contract_address).one_or_none()
    if not contract or not contract.data or not contract.data.get("state"):
        raise NotFoundError(
            message="Contract not found",
            data={"contract_address": contract_address},
        )

    # Create upgrade transaction
    tx_hash = "0x" + secrets.token_hex(32)
    upgrade_tx = Transactions(
        hash=tx_hash,
        status=TransactionStatus.PENDING,
        from_address=None,
        to_address=contract_address,
        input_data=None,
        data={"new_code": new_code},
        consensus_data=None,
        nonce=None,
        value=0,
        type=TransactionType.UPGRADE_CONTRACT.value,
        gaslimit=None,
        leader_only=True,
        r=None,
        s=None,
        v=None,
        appeal_failed=None,
        consensus_history=None,
        timestamp_appeal=None,
        appeal_processing_time=None,
        contract_snapshot=None,
        config_rotation_rounds=None,
        num_of_initial_validators=None,
        last_vote_timestamp=None,
        rotation_count=None,
        leader_timeout_validators=None,
    )
    session.add(upgrade_tx)
    session.commit()

    return {
        "transaction_hash": tx_hash,
        "message": "Upgrade queued. Poll eth_getTransactionReceipt for completion.",
    }


def cancel_transaction(
    session: Session,
    transaction_hash: str,
    msg_handler,
    signature: str | None = None,
    admin_key: str | None = None,
) -> dict:
    """
    Cancel a pending or activated transaction. Returns immediately with status.

    Access control:
    - Local (no env vars): open access
    - Hosted/Self-hosted: admin_key allows ANY transaction, signature allows own transactions

    Args:
        session: Database session
        transaction_hash: Hash of the transaction to cancel
        msg_handler: Message handler for WebSocket notifications
        signature: Hex-encoded signature from tx sender (required in hosted mode unless admin_key)
        admin_key: Admin API key for full access to any transaction

    Returns:
        dict with transaction_hash and status
    """
    from backend.database_handler.models import Transactions
    from eth_account.messages import encode_defunct
    from eth_account import Account
    from web3 import Web3
    import os

    # Validate transaction hash format
    if (
        not transaction_hash
        or not transaction_hash.startswith("0x")
        or len(transaction_hash) != 66
    ):
        raise JSONRPCError(
            code=-32602,
            message="Invalid transaction hash format",
            data={},
        )

    # Look up the transaction
    transaction = (
        session.query(Transactions).filter_by(hash=transaction_hash).one_or_none()
    )
    if not transaction:
        raise NotFoundError(
            message=TRANSACTION_NOT_FOUND_MESSAGE,
            data={"transaction_hash": transaction_hash},
        )

    is_hosted = os.getenv("VITE_IS_HOSTED") == "true"
    admin_api_key = os.getenv("ADMIN_API_KEY")

    # Check if authorization is needed (hosted or self-hosted with key configured)
    needs_auth = is_hosted or admin_api_key

    if needs_auth:
        # Option 1: Admin key grants full access to ANY transaction
        if admin_api_key and admin_key == admin_api_key:
            pass  # Authorized - proceed with cancel

        # Option 2: Signature from tx sender grants access to own transactions
        elif signature:
            if not transaction.from_address:
                raise JSONRPCError(
                    code=-32000,
                    message="Transaction has no sender - only admin key can cancel",
                    data={},
                )

            try:
                # Message: keccak256("cancel_transaction" + tx_hash_bytes)
                # tx_hash is unique, so no nonce needed for replay protection
                message_hash = Web3.keccak(
                    b"cancel_transaction" + Web3.to_bytes(hexstr=transaction_hash)
                )
                message = encode_defunct(primitive=message_hash)
                signer = Account.recover_message(message, signature=signature)

                if signer.lower() != transaction.from_address.lower():
                    raise JSONRPCError(
                        code=-32000,
                        message="Only transaction sender can cancel",
                        data={"signer": signer, "sender": transaction.from_address},
                    )
            except JSONRPCError:
                raise
            except Exception as e:
                raise JSONRPCError(
                    code=-32000,
                    message=f"Invalid signature: {e!s}",
                    data={},
                ) from e
        else:
            raise JSONRPCError(
                code=-32000,
                message="Cancel requires admin key or sender signature",
                data={},
            )

    # Atomic cancel - only succeeds if tx is still pending/activated and not claimed by worker
    was_cancelled = TransactionsProcessor.cancel_transaction_if_available(
        session, transaction_hash
    )

    if not was_cancelled:
        raise JSONRPCError(
            code=-32000,
            message="Transaction cannot be cancelled: already being processed or in a terminal state",
            data={
                "transaction_hash": transaction_hash,
                "status": transaction.status.value,
            },
        )

    # Refund sender for payable transactions that were never activated
    tx_val = transaction.value if isinstance(transaction.value, int) else 0
    if tx_val > 0 and transaction.from_address:
        AccountsManager(session).refund_tx_value(
            transaction_hash, transaction.from_address
        )
    if transaction.from_address:
        AccountsManager(session).cancel_tx_fee_accounting_once(
            transaction_hash, transaction.from_address, "canceled"
        )
    session.commit()

    # Notify frontend via WebSocket
    msg_handler.send_transaction_status_update(transaction_hash, "CANCELED")

    return {
        "transaction_hash": transaction_hash,
        "status": "CANCELED",
    }


####### GEN ENDPOINTS #######
async def get_contract_schema(
    session: Session,
    genvm_manager: GenVMManager,
    msg_handler: IMessageHandler,
    contract_address: str,
) -> dict:
    try:
        contract_snapshot = ContractSnapshot(contract_address, session)
    except ContractNotFoundError:
        raise NotFoundError(
            message=f"Contract {contract_address} not found",
            data={"contract_address": contract_address},
        )
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
    try:
        code_b64 = fetch_deployed_code_b64(session, contract_address)
    except ContractNotFoundError:
        raise NotFoundError(
            message=f"Contract {contract_address} not found",
            data={"contract_address": contract_address},
        )
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
            raise JSONRPCError(
                code=-32002,
                message="No validators available to execute the call",
            )

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


def _state_status_from_call_params(params: dict) -> str:
    """Map public call state selectors to Studio's current internal buckets."""
    status = params.get("status")
    if status is not None:
        if status == "decided":
            return "accepted"
        if status == "finalized":
            return "finalized"
        raise JSONRPCError(
            code=-32602,
            message="Invalid status: must be 'decided' or 'finalized'",
            data={},
        )

    # Legacy Studio selector. Preserve old fallback semantics: only
    # latest-final changes the bucket; all other/absent values read decided state.
    transaction_hash_variant = params.get("transaction_hash_variant")
    if transaction_hash_variant == "latest-final":
        return "finalized"
    return "accepted"


async def gen_call(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    params: dict,
) -> str:
    singleflight_key = _gen_call_singleflight_key(params)
    if singleflight_key is None:
        return await _execute_gen_call_with_admission(
            session,
            accounts_manager,
            msg_handler,
            transactions_parser,
            validators_manager,
            genvm_manager,
            params,
        )

    async with _gen_call_singleflight_lock:
        task = _gen_call_singleflight_tasks.get(singleflight_key)
        if task is None:
            task = asyncio.create_task(
                _run_singleflight_gen_call(
                    singleflight_key,
                    session,
                    accounts_manager,
                    msg_handler,
                    transactions_parser,
                    validators_manager,
                    genvm_manager,
                    params,
                )
            )
            _gen_call_singleflight_tasks[singleflight_key] = task
        else:
            _gen_call_singleflight_logger.debug(
                "Coalescing duplicate gen_call read for key %s",
                singleflight_key[:12],
            )

    return await asyncio.shield(task)


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
    to_address = params.get("to") if isinstance(params, dict) else None
    async with _admit_genvm_call("sim_call", to_address):
        receipt = await _execute_call_with_snapshot(
            session,
            accounts_manager,
            msg_handler,
            transactions_parser,
            validators_manager,
            genvm_manager,
            params,
        )
    return TransactionsProcessor._json_safe_numbers(receipt.to_dict())


async def sim_estimate_transaction_fees(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    genvm_manager: GenVMManager,
    params: dict,
) -> dict:
    estimate_params = _with_default_simulation_fees(params)
    if isinstance(estimate_params, dict):
        estimate_params = {
            **estimate_params,
            "_allow_low_execution_budget_for_estimate": True,
            "_discover_message_allocations_for_estimate": True,
        }
    receipt = await sim_call(
        session=session,
        accounts_manager=accounts_manager,
        msg_handler=msg_handler,
        transactions_parser=transactions_parser,
        validators_manager=validators_manager,
        genvm_manager=genvm_manager,
        params=estimate_params,
    )
    genvm_result = receipt.get("genvm_result") or {}
    fee_accounting = (
        genvm_result.get(FEE_ACCOUNTING_KEY) if isinstance(genvm_result, dict) else {}
    ) or {}
    return TransactionsProcessor._json_safe_numbers(
        {
            "scenario": _first_present(params, "scenario", "scenarioName") or "default",
            "receipt": receipt,
            "feeAccounting": fee_accounting,
            "feeReport": fee_accounting.get("execution_fee_report") or {},
            "recommendedPreset": fee_accounting.get("recommended_fee_preset") or {},
        }
    )


def _with_default_simulation_fees(params: dict) -> dict:
    if not isinstance(params, dict):
        return params
    fees = params.get("fees") if isinstance(params.get("fees"), dict) else {}
    has_fee_params = any(
        key in params
        for key in (
            "fees_distribution",
            "feesDistribution",
            "message_allocations",
            "messageAllocations",
            "fee_value",
            "feeValue",
        )
    ) or any(
        key in fees
        for key in (
            "distribution",
            "fees_distribution",
            "feesDistribution",
            "message_allocations",
            "messageAllocations",
            "fee_value",
            "feeValue",
        )
    )
    if has_fee_params:
        return params

    updated = dict(params)
    updated["fees"] = studio_fee_config(StudioFeePolicy.from_env())["defaultFees"]
    return updated


def _stage_simulated_call_value(
    contract_snapshot: ContractSnapshot, call_value: int
) -> None:
    if call_value <= 0:
        return

    contract_snapshot.balance = int(
        getattr(contract_snapshot, "balance", 0) or 0
    ) + int(call_value)


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
    origin_address = params.get("origin_address")
    call_value = int(params.get("value", "0x0"), 16) if params.get("value") else 0
    simulation_fee_accounting = _simulation_fee_accounting(
        params,
        sender=from_address,
        user_value=call_value,
    )
    genvm_fee_accounting = _effective_simulation_fee_accounting_for_genvm(
        simulation_fee_accounting
    )
    if not accounts_manager.is_valid_address(from_address):
        raise InvalidAddressError(from_address)

    if type == "deploy":
        deployment_count = (
            TransactionsProcessor(session).get_successful_ghost_creation_count()
            if session is not None
            else 0
        )
        to_address = GhostFactoryConfig.from_env().address_for(
            int(params.get("salt_nonce", params.get("saltNonce", 0)) or 0),
            deployment_count,
            namespace=from_address,
        )

    if not accounts_manager.is_valid_address(to_address):
        raise InvalidAddressError(to_address)

    # Rate limit per contract address — reject early before acquiring resources
    _check_rate_limit(to_address)

    state_status = _state_status_from_call_params(params)

    # Get a validator
    if len(validators_snapshot.nodes) > 0:
        validator = validators_snapshot.nodes[0].validator
    else:
        raise JSONRPCError(
            code=-32002,
            message="No validators available to execute the gen_call",
        )

    sc_raw = params.get("sim_config")
    _validate_genvm_executor_selector(sc_raw)
    _reject_genvm_executor_selector_unless_deploy(
        sc_raw,
        is_deploy=type == "deploy",
    )
    sim_config = SimConfig.from_dict(sc_raw) if sc_raw else None
    override_transaction_datetime: bool = (
        sim_config is not None and sim_config.genvm_datetime is not None
    )

    def create_node() -> Node:
        # A fee estimate can execute twice: first to discover exact message
        # keys and then to meter them. Each pass must start from the same DB
        # snapshot; _SnapshotView writes through to its ContractSnapshot.
        if type == "deploy":
            contract_snapshot = ContractSnapshot(None, session)
            contract_snapshot.contract_address = to_address
            contract_snapshot.balance = 0
            contract_snapshot.states = {"accepted": {}, "finalized": {}}
            contract_snapshot.genvm_executor_selector = (
                sim_config.genvm_executor_selector if sim_config else None
            )
        else:
            contract_snapshot = ContractSnapshot(to_address, session)
        if type in {"write", "deploy"}:
            _stage_simulated_call_value(contract_snapshot, call_value)
        return Node(
            contract_snapshot=contract_snapshot,
            contract_snapshot_factory=partial(ContractSnapshot, session=session),
            validator_mode=ExecutionMode.LEADER,
            validator=validator,
            leader_receipt=None,
            msg_handler=msg_handler.with_client_session(get_client_session_id()),
            validators_snapshot=validators_snapshot,
            manager=genvm_manager,
        )

    async def execute(active_fee_accounting):
        node = create_node()
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
            return await node.get_contract_data(
                from_address=from_address,
                calldata=decoded_data.calldata,
                state_status=state_status,
                transaction_datetime=txn_dt,
                origin_address=origin_address,
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
            return await node.run_contract(
                from_address=from_address,
                calldata=decoded_data.calldata,
                transaction_created_at=txn_created_at,
                value=call_value,
                origin_address=origin_address,
                fee_accounting=active_fee_accounting,
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
            return await node.deploy_contract(
                from_address=from_address,
                code_to_deploy=decoded_data.contract_code,
                calldata=decoded_data.calldata,
                transaction_created_at=txn_created_at,
                value=call_value,
                origin_address=origin_address,
                fee_accounting=active_fee_accounting,
            )
        else:
            raise JSONRPCError(
                code=-32602,
                message=f"Invalid type '{type}': must be 'read', 'write', or 'deploy'",
            )

    policy = StudioFeePolicy.from_env()
    discover_message_allocations = bool(
        params.get("_discover_message_allocations_for_estimate")
        and type in {"write", "deploy"}
        and simulation_fee_accounting is not None
        and not simulation_fee_accounting.get("message_allocations")
        and policy.fee_accounting_enabled()
    )

    try:
        # The first pass reveals concrete recipient/call-key pairs without
        # making the transaction envelope permissive. The second pass meters
        # the same execution under the exact discovered allocation roots.
        receipt = await execute(
            None if discover_message_allocations else genvm_fee_accounting
        )
        if (
            discover_message_allocations
            and receipt.execution_result == ExecutionResultStatus.SUCCESS
        ):
            simulation_fee_accounting = fee_accounting_with_discovered_messages(
                simulation_fee_accounting,
                receipt,
                policy,
            )
            genvm_fee_accounting = _effective_simulation_fee_accounting_for_genvm(
                simulation_fee_accounting
            )
            receipt = await execute(genvm_fee_accounting)
    except ContractNotFoundError as e:
        raise NotFoundError(
            message=f"Contract {e.address} not found",
            data={"contract_address": e.address},
        ) from e

    if simulation_fee_accounting is not None:
        receipt.genvm_result = dict(receipt.genvm_result or {})
        receipt.genvm_result["fee_accounting"] = record_execution_fee_consumption(
            simulation_fee_accounting,
            receipt,
        )

    # Return the result of the write method
    if receipt.execution_result != ExecutionResultStatus.SUCCESS:
        raise JSONRPCError(
            code=-32000,
            message="execution failed",
            data={"receipt": receipt.to_dict(), "params": params},
        )

    return receipt


####### ETH ENDPOINTS #######
def get_balance(
    accounts_manager: AccountsManager, account_address: str, block_tag: str = "latest"
) -> str:
    if not accounts_manager.is_valid_address(account_address):
        raise InvalidAddressError(
            account_address, f"Invalid address from_address: {account_address}"
        )
    account_balance = accounts_manager.get_account_balance(account_address)
    return hex(account_balance)


def get_transaction_count(
    transactions_processor: TransactionsProcessor, address: str, block: str = "latest"
) -> str:
    return hex(transactions_processor.get_transaction_count(address))


def get_transaction_by_hash(
    transactions_processor: TransactionsProcessor,
    transaction_hash: str,
    sim_config: dict | None = None,
) -> dict:
    transaction = transactions_processor.get_transaction_by_hash(
        transaction_hash, sim_config, include_contract_snapshot=False
    )

    if transaction is None:
        raise NotFoundError(
            message=f"Transaction {transaction_hash} not found",
            data={"hash": transaction_hash},
        )
    return _sanitize_rpc_private_keys(transaction)


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
    return _sanitize_rpc_private_keys(transaction)


def get_transaction_status(
    transactions_processor: TransactionsProcessor, transaction_hash: str | dict
) -> str | dict:
    return_details = isinstance(transaction_hash, dict)
    if return_details:
        transaction_hash = transaction_hash.get("txId") or transaction_hash.get("tx_id")
        if not isinstance(transaction_hash, str) or not transaction_hash:
            raise JSONRPCError(code=-32602, message="txId is required", data={})
    elif not isinstance(transaction_hash, str) or not transaction_hash:
        raise JSONRPCError(
            code=-32602,
            message="transaction hash must be a string or an object containing txId",
            data={},
        )

    status = transactions_processor.get_transaction_status(transaction_hash)
    if status is None:
        raise NotFoundError(
            message=f"Transaction {transaction_hash} not found",
            data={"hash": transaction_hash},
        )
    # Node v0.6 uses an object request and returns the canonical status payload.
    # Keep the original positional-string response for deployed Studio clients
    # (notably Rally) until they migrate to the train interface.
    return status if return_details else status["status"]


def get_transaction_status_details(
    transactions_processor: TransactionsProcessor, transaction_hash: str
) -> dict:
    status = transactions_processor.get_transaction_status(transaction_hash)
    if status is None:
        raise NotFoundError(
            message=f"Transaction {transaction_hash} not found",
            data={"hash": transaction_hash},
        )
    return status


_DECISION_STATUSES = {
    TransactionStatus.ACCEPTED.value,
    TransactionStatus.UNDETERMINED.value,
    TransactionStatus.LEADER_TIMEOUT.value,
    TransactionStatus.VALIDATORS_TIMEOUT.value,
}

_PROTOCOL_TRANSACTION_STATUS_NAMES = (
    "Uninitialized",
    "Pending",
    "Proposing",
    "Committing",
    "Revealing",
    "Accepted",
    "Undetermined",
    "Finalized",
    "Canceled",
    "AppealRevealing",
    "AppealCommitting",
    "ValidatorsTimeout",
    "LeaderTimeout",
    "LeaderRevealing",
)
_RESOLUTION_ACTION_NAMES = (
    "NoOp",
    "Cancel",
    "ReplaceActor",
    "RotateLeader",
    "ResolveAppeal",
    "MaterializeDecision",
    "Finalize",
)
_RESOLUTION_SOURCE_NAMES = (
    "Unspecified",
    "ActivationInsufficientValidators",
    "ProposalHanging",
    "LeaderReceiptTimeout",
    "CommitHanging",
    "LeaderRevealHanging",
    "FullReveal",
    "RevealDeadline",
    "AppealCommitHanging",
    "AppealFullReveal",
    "AppealRevealDeadline",
    "SelectionDepleted",
)


def _transaction_appeal_deadline(transaction: dict) -> float | None:
    started_at = transaction.get("timestamp_awaiting_finalization")
    if started_at is not None and str(transaction.get("execution_mode") or "") in {
        "LEADER_ONLY",
        "LEADER_SELF_VALIDATOR",
    }:
        return float(started_at)
    decision = latest_decision_metadata(transaction.get("consensus_history"))
    if decision is not None:
        try:
            deadline = int(decision.get("appealDeadline") or 0)
        except (TypeError, ValueError):
            deadline = 0
        if deadline > 0:
            return float(deadline)
    if started_at is None:
        return None
    finality_window = int(os.environ.get("VITE_FINALITY_WINDOW", "1800"))
    failed_reduction = float(
        os.environ.get("VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION", "0")
    )
    failed_reduction = min(1.0, max(0.0, failed_reduction))
    return (
        float(started_at)
        + float(transaction.get("appeal_processing_time") or 0)
        + finality_window
        * ((1.0 - failed_reduction) ** int(transaction.get("appeal_failed") or 0))
    )


def _transaction_decision_id(transaction: dict) -> int:
    return history_current_decision_id(transaction.get("consensus_history"))


def _transaction_resolution_source_code(
    transaction: dict, *, status: str, current_round: int
) -> int:
    if status == TransactionStatus.LEADER_TIMEOUT.value:
        return 3  # LeaderReceiptTimeout

    logical_entries = logical_fee_round_entries(transaction.get("consensus_history"))
    if status == TransactionStatus.UNDETERMINED.value and len(logical_entries) == 1:
        _, entry = logical_entries[0]
        if (
            str(entry.get("consensus_round") or "") == "Undetermined"
            and not entry.get("leader_result")
            and not entry.get("validator_results")
        ):
            # Studio's exact-capacity activation path records the same
            # receipt-less Pending -> Undetermined decision as Consensus's
            # ActivationInsufficientValidators trigger.
            return 1

    appeal_attempt = current_round % 2 == 1
    # Studio materializes these outcomes only after its RevealingState has
    # tallied the complete receipt set. A validator execution timeout is a
    # VoteType.Timeout ballot, not a Consensus RevealDeadline trigger.
    return 9 if appeal_attempt else 6


def get_transaction_lifecycle(
    transactions_processor: TransactionsProcessor,
    params: dict,
) -> dict:
    """Project Studio's stored decision state through the v0.6 lifecycle ABI."""
    if not isinstance(params, dict):
        raise JSONRPCError(code=-32602, message="params must be an object", data={})
    transaction_hash = params.get("txId") or params.get("tx_id")
    if not isinstance(transaction_hash, str) or not transaction_hash:
        raise JSONRPCError(code=-32602, message="txId is required", data={})
    transaction = transactions_processor.get_transaction_by_hash(transaction_hash)
    if transaction is None:
        raise NotFoundError(
            message=f"Transaction {transaction_hash} not found",
            data={"hash": transaction_hash},
        )

    requested_timestamp = params.get("timestamp")
    try:
        evaluated_at = (
            int(time.time())
            if requested_timestamp is None
            else int(requested_timestamp)
        )
    except (TypeError, ValueError) as exc:
        raise JSONRPCError(
            code=-32602,
            message="timestamp must be a non-negative integer",
            data={},
        ) from exc
    if evaluated_at < 0:
        raise JSONRPCError(
            code=-32602,
            message="timestamp must be a non-negative integer",
            data={},
        )

    status = str(transaction.get("status") or "UNINITIALIZED").upper()
    stored_status_code = TRANSACTION_STATUS_CODES.get(status, 0)
    decision_active = status in _DECISION_STATUSES and not bool(
        transaction.get("appealed")
    )
    effects_pending = acceptance_dispatch_pending(
        (transaction.get("data") or {}).get(FEE_ACCOUNTING_KEY)
    )
    current_round = _current_fee_round(transaction.get("consensus_history"))
    decision_id = _transaction_decision_id(transaction) if decision_active else None

    if not decision_active:
        resolution_source_code = 0
    else:
        resolution_source_code = _transaction_resolution_source_code(
            transaction,
            status=status,
            current_round=current_round,
        )

    resolution_action_code = 0
    deadline = _transaction_appeal_deadline(transaction)
    if (
        decision_active
        and not effects_pending
        and deadline is not None
        and evaluated_at >= deadline
        and transactions_processor.is_transaction_finalization_head(transaction_hash)
    ):
        resolution_action_code = 6  # Finalize

    return {
        "storedStatus": _PROTOCOL_TRANSACTION_STATUS_NAMES[stored_status_code],
        "storedStatusCode": stored_status_code,
        # ResolutionKernel leaves a decision in its stored status until the
        # separately reported Finalize action is actually committed.
        "projectedStatus": _PROTOCOL_TRANSACTION_STATUS_NAMES[stored_status_code],
        "projectedStatusCode": stored_status_code,
        "resolutionAction": _RESOLUTION_ACTION_NAMES[resolution_action_code],
        "resolutionActionCode": resolution_action_code,
        "resolutionSource": _RESOLUTION_SOURCE_NAMES[resolution_source_code],
        "resolutionSourceCode": resolution_source_code,
        "decisionId": str(decision_id) if decision_id is not None else None,
        "decisionActive": decision_active,
        "evaluatedAt": evaluated_at,
    }


def estimate_latest_appeal_charge(
    transactions_processor: TransactionsProcessor,
    params: dict,
) -> dict:
    """Return the exact decision-bound appeal quote used by Studio admission."""
    if not isinstance(params, dict):
        raise JSONRPCError(code=-32602, message="params must be an object", data={})
    transaction_hash = params.get("txId") or params.get("tx_id")
    if not isinstance(transaction_hash, str) or not transaction_hash:
        raise JSONRPCError(code=-32602, message="txId is required", data={})
    transaction = transactions_processor.get_transaction_by_hash(transaction_hash)
    if transaction is None:
        raise NotFoundError(
            message=f"Transaction {transaction_hash} not found",
            data={"hash": transaction_hash},
        )

    status = str(transaction.get("status") or "")
    if (
        status not in _DECISION_STATUSES
        or bool(transaction.get("appealed"))
        or has_terminal_validator_appeal(transaction.get("consensus_history"))
    ):
        raise InvalidTransactionError("CanNotAppeal")
    deadline = _transaction_appeal_deadline(transaction)
    if deadline is not None and time.time() >= deadline:
        raise InvalidTransactionError("CanNotAppeal")

    fee_accounting = (transaction.get("data") or {}).get(FEE_ACCOUNTING_KEY)
    if fee_accounting is None:
        raise InvalidTransactionError("FeeAccountingMissing")
    if acceptance_dispatch_pending(fee_accounting):
        raise InvalidTransactionError("CanNotAppeal")
    session = getattr(transactions_processor, "session", None)
    frozen_pool_addresses = fee_accounting.get("selection_pool_addresses")
    live_pool_addresses = None
    if session is not None and isinstance(frozen_pool_addresses, list):
        live_pool_addresses = [
            validator.get("address")
            for validator in ValidatorsRegistry(session).get_all_validators(
                include_private_key=False
            )
        ]
    frozen_pool_count = fee_accounting.get("selection_pool_count")
    validator_count = (
        int(frozen_pool_count)
        if frozen_pool_count is not None
        else (
            ValidatorsRegistry(session).count_validators()
            if session is not None
            else int(
                fee_accounting.get("num_of_initial_validators")
                or transaction.get("num_of_initial_validators")
                or 5
            )
        )
    )
    current_round = _current_fee_round(transaction.get("consensus_history"))
    available_appeal_validators = _available_appeal_validator_count(
        transaction,
        validator_count,
        frozen_pool_addresses=frozen_pool_addresses,
        live_pool_addresses=live_pool_addresses,
    )
    if (
        status
        in {
            TransactionStatus.ACCEPTED.value,
            TransactionStatus.VALIDATORS_TIMEOUT.value,
        }
        and available_appeal_validators == 0
    ):
        raise InvalidTransactionError("CanNotAppeal")
    if (
        status == TransactionStatus.UNDETERMINED.value
        and available_appeal_validators is not None
        and available_appeal_validators
        < VALIDATORS_PER_ROUND[min(current_round + 1, len(VALIDATORS_PER_ROUND) - 1)]
    ):
        raise InvalidTransactionError("CanNotAppeal")
    live_seats = None
    if status == TransactionStatus.LEADER_TIMEOUT.value:
        live_validators = transaction.get("leader_timeout_validators")
        if isinstance(live_validators, list):
            # Studio persists the survivors after removing the timed-out
            # leader; Consensus applies its eligibility gate to the original
            # committee before that removal.
            live_seats = len(live_validators) + 1
    charge = calculate_appeal_charge(
        fee_accounting["fees_distribution"],
        current_round=current_round,
        status=status,
        terminal_committee_upper_bound=max(
            0,
            validator_count
            - _normal_leader_count(transaction.get("consensus_history")),
        ),
        available_appeal_validators=available_appeal_validators,
        leader_timeout_live_seats=live_seats,
        policy=funding_policy_for_accounting(
            fee_accounting,
            StudioFeePolicy.from_env(),
        ),
    )
    decision_id = _transaction_decision_id(transaction)
    return {
        "decisionId": str(decision_id),
        "bond": str(int(charge["bond"])),
        "funding": str(int(charge["funding"])),
        "appealDeadline": str(math.ceil(deadline)) if deadline is not None else "0",
    }


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

    async with _admit_genvm_call("eth_call", to_address):
        decoded_data = transactions_parser.decode_method_call_data(data)

        async with validators_manager.snapshot() as snapshot:
            if len(snapshot.nodes) == 0:
                raise JSONRPCError(
                    code=-32000,
                    message="No validators available to execute eth_call",
                    data={"reason": "no_validators"},
                )
            as_validator = snapshot.nodes[0].validator
            try:
                target_contract_snapshot = ContractSnapshot(to_address, session)
            except ContractNotFoundError:
                raise NotFoundError(
                    message=f"Contract {to_address} not found",
                    data={"contract_address": to_address},
                )
            node = Node(  # Mock node just to get the data from the GenVM
                contract_snapshot=target_contract_snapshot,
                contract_snapshot_factory=partial(ContractSnapshot, session=session),
                validator_mode=ExecutionMode.LEADER,
                validator=as_validator,
                leader_receipt=None,
                msg_handler=msg_handler.with_client_session(get_client_session_id()),
                validators_snapshot=snapshot,
                manager=genvm_manager,
            )

            try:
                receipt = await node.get_contract_data(
                    from_address=as_validator.address,
                    calldata=decoded_data.calldata,
                )
            except ContractNotFoundError as e:
                raise NotFoundError(
                    message=f"Contract {e.address} not found",
                    data={"contract_address": e.address},
                ) from e

    if receipt.execution_result != ExecutionResultStatus.SUCCESS:
        raise JSONRPCError(
            code=-32000, message="execution failed", data={"receipt": receipt.to_dict()}
        )
    return eth_utils.hexadecimal.encode_hex(receipt.result[1:])


def _fee_metadata(decoded_rollup_transaction: DecodedRollupTransaction) -> dict:
    if (
        decoded_rollup_transaction.data is None
        or isinstance(decoded_rollup_transaction.data, DecodedsubmitAppealDataArgs)
        or isinstance(decoded_rollup_transaction.data, DecodedTopUpFeesDataArgs)
        or isinstance(
            decoded_rollup_transaction.data, DecodedFinalizeTransactionDataArgs
        )
        or not hasattr(decoded_rollup_transaction.data, "args")
        or decoded_rollup_transaction.data.args is None
    ):
        return {}

    args = decoded_rollup_transaction.data.args
    if args.fees_distribution is None and decoded_rollup_transaction.fee_value == 0:
        return {}

    metadata = {
        "fee_value": decoded_rollup_transaction.fee_value,
        "user_value": args.user_value,
        "valid_until": args.valid_until,
        "salt_nonce": args.salt_nonce,
        "fees_distribution": args.fees_distribution,
        "message_allocations_count": args.message_allocations_count,
    }
    metadata[FEE_ACCOUNTING_KEY] = create_fee_accounting(
        fees_distribution=args.fees_distribution,
        message_allocations=args.message_allocations,
        num_of_validators=args.num_of_initial_validators,
        submitted_value=decoded_rollup_transaction.total_spend,
        user_value=int(args.user_value or 0),
        sender=decoded_rollup_transaction.from_address,
        policy=StudioFeePolicy.from_env(),
    )
    return metadata


def _funded_max_rotations(
    decoded_rollup_transaction: DecodedRollupTransaction,
    requested_max_rotations: int,
) -> int:
    """Mirror Consensus' submission-time rotation-capacity clamp.

    ``initialRotations`` remains the transaction-wide ceiling. Consensus
    clamps it to the largest funded entry so later normal rounds retain their
    paid capacity; each round is separately bounded by its own schedule entry
    at runtime.
    """

    data = decoded_rollup_transaction.data
    if (
        data is None
        or isinstance(
            data,
            (
                DecodedsubmitAppealDataArgs,
                DecodedTopUpFeesDataArgs,
                DecodedFinalizeTransactionDataArgs,
            ),
        )
        or not hasattr(data, "args")
        or data.args is None
        or data.args.fees_distribution is None
    ):
        return int(requested_max_rotations)

    rotations = normalize_fees_distribution(data.args.fees_distribution)["rotations"]
    if not rotations:
        return int(requested_max_rotations)
    return min(int(requested_max_rotations), max(int(value) for value in rotations))


# `DebugMode` is ordered least- to most-permissive, and the manager gates
# `reroute_to` on `debug_mode >= Safe` (implementation/src/manager/run.rs).
# Comparing positions states that rule directly instead of enumerating the
# levels above it, which is how `safe-unbounded` -- the level studio's own run
# path resolves to -- gets left out of a hand-written list.
_DEBUG_MODE_ORDER: Final = get_args(base_host.DebugMode)
_MIN_REROUTE_TO_DEBUG_MODE: Final = _DEBUG_MODE_ORDER.index("safe")


def _genvm_executor_selector_is_present(sim_config: dict | None) -> bool:
    """A missing key, `None`, or `""` all mean "unset" and are ignored.

    Anything else -- including a non-string falsy value like `0`, `False`,
    `[]`, or `{}` -- counts as present, so it reaches the type/grammar checks
    below instead of silently being treated the same as "unset".
    """
    if not sim_config:
        return False
    value = sim_config.get("genvm_executor_selector")
    if value is None:
        return False
    if isinstance(value, str) and not value:
        return False
    return True


def _validate_genvm_executor_selector(sim_config: dict | None) -> None:
    """`sim_config.genvm_executor_selector` pins the contract to a GenVM
    executor version or `re:` selector.

    The manager honors it only under `debug_mode >= safe` and ignores it
    silently otherwise, so reject the transaction instead of running it on
    an executor the caller did not ask for.
    """
    if not _genvm_executor_selector_is_present(sim_config):
        return
    genvm_executor_selector = sim_config["genvm_executor_selector"]
    debug_mode = _genvm_debug_mode()
    if (
        debug_mode not in _DEBUG_MODE_ORDER
        or _DEBUG_MODE_ORDER.index(debug_mode) < _MIN_REROUTE_TO_DEBUG_MODE
    ):
        raise JSONRPCError(
            code=-32602,
            message=(
                "sim_config.genvm_executor_selector requires genvm debug mode "
                "(GENVM_DEBUG_MODE)"
            ),
            data={"genvm_executor_selector": genvm_executor_selector},
        )
    # The pin is persisted on the contract and only read back much later, by
    # `resolve_call_contract_executor` in the middle of a run. Validating it here
    # turns an unusable pin into a rejected transaction instead of a contract
    # that fails every call it takes part in.
    # `fullmatch`, not `match`: `$` also matches in front of a final newline, so
    # an anchored `match` would let `"v0.2.17\n"` through as a directory name.
    if not isinstance(genvm_executor_selector, str) or not is_valid_executor_selector(
        genvm_executor_selector
    ):
        raise JSONRPCError(
            code=-32602,
            message=(
                "sim_config.genvm_executor_selector is not a valid executor "
                "version or selector"
            ),
            data={"genvm_executor_selector": genvm_executor_selector},
        )


def _reject_genvm_executor_selector_unless_deploy(
    sim_config: dict | None, *, is_deploy: bool
) -> None:
    """`sim_config.genvm_executor_selector` pins the *deployment's* executor;
    the manager ignores it for every other transaction type. Silently
    accepting it elsewhere would look like the pin took effect when it never
    reached the manager at all, so reject it instead of dropping it on the
    floor.
    """
    if is_deploy or not _genvm_executor_selector_is_present(sim_config):
        return
    raise JSONRPCError(
        code=-32602,
        message=(
            "sim_config.genvm_executor_selector is only valid for contract "
            "deployment transactions"
        ),
        data={"genvm_executor_selector": sim_config["genvm_executor_selector"]},
    )


def _validate_fee_envelope(
    decoded_rollup_transaction: DecodedRollupTransaction,
) -> None:
    if (
        decoded_rollup_transaction.data is None
        or isinstance(decoded_rollup_transaction.data, DecodedsubmitAppealDataArgs)
        or isinstance(decoded_rollup_transaction.data, DecodedTopUpFeesDataArgs)
        or isinstance(
            decoded_rollup_transaction.data, DecodedFinalizeTransactionDataArgs
        )
        or not hasattr(decoded_rollup_transaction.data, "args")
        or decoded_rollup_transaction.data.args is None
    ):
        return

    args = decoded_rollup_transaction.data.args
    policy = StudioFeePolicy.from_env()
    if (
        decoded_rollup_transaction.data.function_name == "deploySalted"
        and int(args.salt_nonce or 0) == 0
    ):
        raise InvalidTransactionError("InvalidDeploymentWithSalt")
    if args.fees_distribution is None:
        if policy.fee_accounting_enabled():
            raise InvalidTransactionError("FeesDistributionMissing")
        return

    try:
        validate_transaction_fee_deposit(
            fees_distribution=args.fees_distribution,
            message_allocations=args.message_allocations,
            num_of_validators=args.num_of_initial_validators,
            submitted_value=decoded_rollup_transaction.total_spend,
            user_value=int(args.user_value or 0),
            policy=policy,
        )
    except FeeValidationError as exc:
        raise InvalidTransactionError(str(exc)) from exc


def _sandbox_debit_sender(
    accounts_manager: AccountsManager, from_address: str, amount: int
) -> None:
    if amount <= 0:
        return
    sender_balance = accounts_manager.get_account_balance(from_address)
    if sender_balance < amount:
        accounts_manager.credit_account_balance(from_address, amount - sender_balance)
    accounts_manager.debit_account_balance(from_address, amount)


def _handle_top_up_fees(
    *,
    accounts_manager: AccountsManager,
    transactions_processor: TransactionsProcessor,
    decoded_rollup_transaction: DecodedRollupTransaction,
) -> str:
    assert isinstance(decoded_rollup_transaction.data, DecodedTopUpFeesDataArgs)
    tx_id = _tx_id_to_hex(decoded_rollup_transaction.data.tx_id)
    session = getattr(transactions_processor, "session", None)
    try:
        transactions_processor.apply_transaction_fee_top_up(
            tx_id,
            fees_distribution=decoded_rollup_transaction.data.fees_distribution,
            amount=decoded_rollup_transaction.total_spend,
            sender=decoded_rollup_transaction.from_address,
            policy=StudioFeePolicy.from_env(),
        )
        _sandbox_debit_sender(
            accounts_manager,
            decoded_rollup_transaction.from_address,
            decoded_rollup_transaction.total_spend,
        )
    except FeeValidationError as exc:
        if session is not None:
            session.rollback()
        raise InvalidTransactionError(str(exc)) from exc
    except ValueError as exc:
        if session is not None:
            session.rollback()
        if str(exc) == "TransactionNotFound":
            raise NotFoundError(
                message=TRANSACTION_NOT_FOUND_MESSAGE, data={"hash": tx_id}
            ) from exc
        raise InvalidTransactionError(str(exc)) from exc
    except Exception:
        if session is not None:
            session.rollback()
        raise
    return tx_id


def _handle_appeal_or_top_up_and_submit(
    *,
    accounts_manager: AccountsManager,
    transactions_processor: TransactionsProcessor,
    msg_handler: IMessageHandler,
    decoded_rollup_transaction: DecodedRollupTransaction,
    emit_event: bool = True,
) -> str:
    assert isinstance(decoded_rollup_transaction.data, DecodedsubmitAppealDataArgs)
    tx_id = _tx_id_to_hex(decoded_rollup_transaction.data.tx_id)
    tx = transactions_processor.get_transaction_by_hash(tx_id)
    if tx is None:
        raise NotFoundError(message=TRANSACTION_NOT_FOUND_MESSAGE, data={"hash": tx_id})

    status = str(tx.get("status") or "")
    if status not in {
        TransactionStatus.ACCEPTED.value,
        TransactionStatus.UNDETERMINED.value,
        TransactionStatus.LEADER_TIMEOUT.value,
        TransactionStatus.VALIDATORS_TIMEOUT.value,
    } or bool(tx.get("appealed")):
        raise InvalidTransactionError("CanNotAppeal")
    if has_terminal_validator_appeal(tx.get("consensus_history")):
        raise InvalidTransactionError("CanNotAppeal")
    appeal_deadline = _transaction_appeal_deadline(tx)
    if appeal_deadline is not None:
        # Consensus rejects at `block.timestamp >= appealDeadline`; do the
        # same at the RPC admission boundary instead of relying on the
        # asynchronous finalization worker to win the race first.
        if time.time() >= appeal_deadline:
            raise InvalidTransactionError("CanNotAppeal")
    consensus_history = tx.get("consensus_history")
    expected_decision_id = decoded_rollup_transaction.data.expected_decision_id
    current_round = _current_fee_round(consensus_history)
    # New Studio decisions persist their exact materialized ID. Legacy rows
    # derive the same monotonic ordinal from alternating normal/appeal history.
    current_decision_id = _transaction_decision_id(tx)
    # v0.6 binds every appeal to one exact materialized decision.  Older SDKs
    # emitted selectors without this argument; Studio may still decode those
    # transactions to surface a canonical error, but must never admit the
    # unbound appeal when deployed Consensus would reject it.
    if expected_decision_id is None or int(expected_decision_id) != current_decision_id:
        raise InvalidTransactionError("CanNotAppeal")

    fee_accounting = (tx.get("data") or {}).get(FEE_ACCOUNTING_KEY)
    if fee_accounting is None:
        raise InvalidTransactionError("FeeAccountingMissing")
    if acceptance_dispatch_pending(fee_accounting):
        raise InvalidTransactionError("CanNotAppeal")
    if fee_accounting is not None:
        session = getattr(accounts_manager, "session", None)
        frozen_pool_count = fee_accounting.get("selection_pool_count")
        frozen_pool_addresses = fee_accounting.get("selection_pool_addresses")
        live_pool_addresses = None
        if session is not None and isinstance(frozen_pool_addresses, list):
            live_pool_addresses = [
                validator.get("address")
                for validator in ValidatorsRegistry(session).get_all_validators(
                    include_private_key=False
                )
            ]
        validator_count = (
            int(frozen_pool_count)
            if frozen_pool_count is not None
            else (
                ValidatorsRegistry(session).count_validators()
                if session is not None
                else int(
                    fee_accounting.get("num_of_initial_validators")
                    or tx.get("num_of_initial_validators")
                    or 5
                )
            )
        )
        normal_leader_count = _normal_leader_count(consensus_history)
        available_appeal_validators = _available_appeal_validator_count(
            tx,
            validator_count,
            frozen_pool_addresses=frozen_pool_addresses,
            live_pool_addresses=live_pool_addresses,
        )
        if (
            status
            in {
                TransactionStatus.ACCEPTED.value,
                TransactionStatus.VALIDATORS_TIMEOUT.value,
            }
            and available_appeal_validators == 0
        ):
            raise InvalidTransactionError("CanNotAppeal")
        if (
            status == TransactionStatus.UNDETERMINED.value
            and available_appeal_validators is not None
            and available_appeal_validators
            < VALIDATORS_PER_ROUND[
                min(current_round + 1, len(VALIDATORS_PER_ROUND) - 1)
            ]
        ):
            raise InvalidTransactionError("CanNotAppeal")
        replacement_rotations = None
        leader_timeout_live_seats = None
        if status == TransactionStatus.LEADER_TIMEOUT.value:
            live_validators = tx.get("leader_timeout_validators")
            if isinstance(live_validators, list):
                leader_timeout_live_seats = len(live_validators) + 1

        def prepare_fee_accounting(current_fee_accounting):
            if current_fee_accounting is None:
                raise FeeValidationError("FeeAccountingMissing")
            updated = record_appeal_bond(
                current_fee_accounting,
                amount=decoded_rollup_transaction.total_spend,
                appealer=decoded_rollup_transaction.from_address,
                current_round=current_round,
                status=status,
                fees_distribution=decoded_rollup_transaction.data.fees_distribution,
                top_up_and_submit=decoded_rollup_transaction.data.top_up_and_submit,
                terminal_committee_upper_bound=max(
                    0,
                    validator_count - normal_leader_count,
                ),
                available_appeal_validators=available_appeal_validators,
                replacement_rotations=replacement_rotations,
                leader_timeout_live_seats=leader_timeout_live_seats,
                policy=funding_policy_for_accounting(
                    current_fee_accounting,
                    StudioFeePolicy.from_env(),
                ),
            )
            return updated, int(updated["appeal_bonds"][-1]["surplusRefund"])

    submitted_at = int(time.time())
    failed_reduction = float(
        os.environ.get("VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION", "0")
    )
    failed_reduction = min(1.0, max(0.0, failed_reduction))
    session = getattr(transactions_processor, "session", None)
    try:
        surplus_refund = transactions_processor.admit_transaction_appeal(
            tx_id,
            expected_decision_id=current_decision_id,
            submitted_at=submitted_at,
            appeal_deadline=(
                int(appeal_deadline)
                if appeal_deadline is not None
                else submitted_at + int(os.environ.get("VITE_FINALITY_WINDOW", "1800"))
            ),
            retention_bps=round((1.0 - failed_reduction) * 10_000),
            prepare_fee_accounting=prepare_fee_accounting,
        )
        _sandbox_debit_sender(
            accounts_manager,
            decoded_rollup_transaction.from_address,
            decoded_rollup_transaction.total_spend,
        )
        if surplus_refund > 0:
            accounts_manager.credit_account_balance(
                decoded_rollup_transaction.from_address,
                surplus_refund,
            )
    except FeeValidationError as exc:
        if session is not None:
            session.rollback()
        raise InvalidTransactionError(str(exc)) from exc
    except ValueError as exc:
        if session is not None:
            session.rollback()
        raise InvalidTransactionError("CanNotAppeal") from exc
    except Exception:
        if session is not None:
            session.rollback()
        raise
    if emit_event:
        msg_handler.send_message(
            log_event=_transaction_appeal_updated_event(tx_id),
            log_to_terminal=False,
        )
    return tx_id


def _transaction_appeal_updated_event(tx_id: str) -> LogEvent:
    return LogEvent(
        "transaction_appeal_updated",
        EventType.INFO,
        EventScope.CONSENSUS,
        "Set transaction appealed",
        {"hash": tx_id},
    )


def _handle_finalize_transaction(
    *,
    transactions_processor: TransactionsProcessor,
    decoded_rollup_transaction: DecodedRollupTransaction,
) -> str:
    """Validate an exact-decision finalization request.

    Studio's worker owns the asynchronous state/contract commit. This boundary
    mirrors v0.6 admission: it accepts only the active decision, at or after its
    immutable deadline, and only at the recipient's finalization head. The
    already-eligible row is then consumed by the worker without inventing a
    second Studio-only lifecycle transition.
    """
    assert isinstance(
        decoded_rollup_transaction.data, DecodedFinalizeTransactionDataArgs
    )
    if int(decoded_rollup_transaction.total_spend) != 0:
        # ConsensusMain.finalizeTransaction is nonpayable. Do not silently
        # accept value that an EVM call would reject (or leave it undebited).
        raise InvalidTransactionError("NonPayableCall")
    tx_id = _tx_id_to_hex(decoded_rollup_transaction.data.tx_id)
    transaction = transactions_processor.get_transaction_by_hash(tx_id)
    if transaction is None:
        raise NotFoundError(message=TRANSACTION_NOT_FOUND_MESSAGE, data={"hash": tx_id})

    status = str(transaction.get("status") or "")
    if status not in _DECISION_STATUSES or bool(transaction.get("appealed")):
        raise InvalidTransactionError("FinalizationNotAllowed")
    if acceptance_dispatch_pending(
        (transaction.get("data") or {}).get(FEE_ACCOUNTING_KEY)
    ):
        raise InvalidTransactionError("FinalizationNotAllowed")
    current_decision_id = _transaction_decision_id(transaction)
    expected_decision_id = decoded_rollup_transaction.data.expected_decision_id
    if expected_decision_id is None or int(expected_decision_id) != current_decision_id:
        raise InvalidTransactionError("FinalizationNotAllowed")

    deadline = _transaction_appeal_deadline(transaction)
    if deadline is None or time.time() < deadline:
        raise InvalidTransactionError("FinalizationNotAllowed")
    if not transactions_processor.is_transaction_finalization_head(tx_id):
        raise InvalidTransactionError("FinalizationNotAllowed")
    return tx_id


def _tx_id_to_hex(tx_id: str | bytes) -> str:
    return "0x" + tx_id.hex() if isinstance(tx_id, bytes) else tx_id


def _current_fee_round(consensus_history: dict | None) -> int:
    return completed_consensus_round_index(consensus_history)


def _normal_leader_count(consensus_history: dict | None) -> int:
    # Consensus excludes every prior *normal-round* leader from the terminal
    # replacement committee. Studio compacts a leader appeal's hidden appeal
    # round and replay into one history entry, so raw list parity is not a safe
    # way to identify normal rounds after the first leader appeal.
    rotations_by_round = actual_leader_rotations_by_round(consensus_history)
    return sum(
        1 + int(rotations_by_round.get(logical_round, 0))
        for logical_round, _entry in logical_fee_round_entries(consensus_history)
        if logical_round % 2 == 0
    )


def _available_appeal_validator_count(
    transaction: dict,
    frozen_pool_count: int,
    *,
    frozen_pool_addresses: list[str] | None = None,
    live_pool_addresses: list[str] | None = None,
) -> int | None:
    """Mirror Studio's fresh-juror pool; unknown legacy state stays conservative."""
    used_addresses = ConsensusAlgorithm.get_consumed_validator_addresses(
        transaction.get("consensus_history"),
        transaction.get("consensus_data"),
    )

    if not used_addresses:
        return None
    if isinstance(frozen_pool_addresses, list):
        eligible = {
            str(address).lower() for address in frozen_pool_addresses if address
        }
        if isinstance(live_pool_addresses, list):
            eligible &= {
                str(address).lower() for address in live_pool_addresses if address
            }
        return len(eligible - used_addresses)
    return max(0, int(frozen_pool_count) - len(used_addresses))


def _simulation_fee_accounting(
    params: dict,
    *,
    sender: str,
    user_value: int,
) -> dict | None:
    fees = params.get("fees") if isinstance(params.get("fees"), dict) else {}
    fees_distribution = _first_present(
        params,
        "fees_distribution",
        "feesDistribution",
    ) or _first_present(fees, "distribution", "fees_distribution", "feesDistribution")
    message_allocations = _first_present(
        params,
        "message_allocations",
        "messageAllocations",
    )
    if message_allocations is None:
        message_allocations = _first_present(
            fees,
            "message_allocations",
            "messageAllocations",
        )
    raw_fee_value = _first_present(params, "fee_value", "feeValue")
    if raw_fee_value is None:
        raw_fee_value = _first_present(fees, "fee_value", "feeValue")

    if fees_distribution is None and not message_allocations and raw_fee_value is None:
        return None

    fees_distribution = fees_distribution or {}
    message_allocations = message_allocations or []
    num_of_initial_validators = _int_param(
        _first_present(params, "num_of_initial_validators", "numOfInitialValidators"),
        5,
    )
    policy = StudioFeePolicy.from_env()
    fee_value = _int_param(raw_fee_value, None)
    if fee_value is None:
        fee_value = required_fee_deposit(
            fees_distribution,
            num_of_initial_validators,
            policy,
        )

    try:
        return create_fee_accounting(
            fees_distribution=fees_distribution,
            message_allocations=message_allocations,
            num_of_validators=num_of_initial_validators,
            submitted_value=int(user_value) + int(fee_value),
            user_value=int(user_value),
            sender=sender,
            policy=policy,
            allow_low_execution_budget=bool(
                params.get("_allow_low_execution_budget_for_estimate")
            ),
        )
    except FeeValidationError as exc:
        raise JSONRPCError(code=-32602, message=str(exc), data={}) from exc


def _effective_simulation_fee_accounting_for_genvm(
    accounting: dict | None,
) -> dict | None:
    if not accounting:
        return accounting

    snapshot = accounting.get("policy_snapshot")
    policy = (
        StudioFeePolicy.from_snapshot(snapshot)
        if isinstance(snapshot, dict)
        else StudioFeePolicy.from_env()
    )
    fees = normalize_fees_distribution(accounting.get("fees_distribution") or {})
    execution_budget_per_round = int(fees["executionBudgetPerRound"])
    # Consensus admits against the proposal-only receipt floor, while GenVM
    # reserves proposal/reveal/nondeterministic-output start costs before user
    # code. Estimation must temporarily satisfy the latter or it cannot run far
    # enough to recommend a realistic budget.
    floor = policy.genvm_start_budget_floor()
    if execution_budget_per_round <= 0 or execution_budget_per_round >= floor:
        return accounting

    adjusted = copy.deepcopy(accounting)
    adjusted_fees = dict(fees)
    adjusted_fees["executionBudgetPerRound"] = floor
    adjusted["fees_distribution"] = adjusted_fees
    adjusted["execution_budget_total"] = floor * get_leader_rounds(adjusted_fees)
    return adjusted


def _first_present(source: dict | None, *keys: str):
    if not isinstance(source, dict):
        return None
    for key in keys:
        if key in source:
            return source[key]
    return None


def _int_param(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def send_raw_transaction(
    session: Session,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    consensus_service: ConsensusService,
    signed_rollup_transaction: str,
    sim_config: dict | None = None,
) -> str:
    """Admit one signed envelope atomically with its protocol mutation."""

    try:
        return _send_raw_transaction_impl(
            session,
            msg_handler,
            transactions_parser,
            consensus_service,
            signed_rollup_transaction,
            sim_config,
        )
    except _EvmExecutionReverted as reverted:
        # Solidity execution reverts roll back the protocol mutation, but the
        # execution-chain envelope is still mined and consumes the account
        # nonce. Start a fresh DB transaction after the handler rollback and
        # persist the status-0 receipt boundary.
        rollback = getattr(session, "rollback", None)
        if callable(rollback):
            rollback()
        transactions_processor = TransactionsProcessor(session)
        try:
            prior_result = transactions_processor.begin_evm_envelope(
                reverted.transaction_hash,
                reverted.from_address,
                reverted.nonce,
            )
        except ValueError as exc:
            raise InvalidTransactionError(str(exc)) from exc
        if prior_result is None:
            transactions_processor.record_evm_envelope(
                reverted.transaction_hash,
                reverted.from_address,
                reverted.nonce,
                reverted.transaction_hash,
                to_address=reverted.to_address,
                success=False,
                error=str(reverted.reason),
            )
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        return reverted.transaction_hash
    except Exception:
        rollback = getattr(session, "rollback", None)
        if callable(rollback):
            rollback()
        raise


def _send_raw_transaction_impl(
    session: Session,
    msg_handler: IMessageHandler,
    transactions_parser: TransactionParser,
    consensus_service: ConsensusService,
    signed_rollup_transaction: str,
    sim_config: dict | None = None,
) -> str:
    """Persist a raw transaction using a request-scoped session."""
    _validate_genvm_executor_selector(sim_config)

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

    transaction_chain_id = getattr(decoded_rollup_transaction, "chain_id", None)
    if (
        transaction_chain_id is not None
        and int(transaction_chain_id) != get_simulator_chain_id()
    ):
        raise InvalidTransactionError("InvalidChainId")

    protocol_data_types = (
        DecodedRollupTransactionData,
        DecodedsubmitAppealDataArgs,
        DecodedTopUpFeesDataArgs,
        DecodedFinalizeTransactionDataArgs,
    )
    lifecycle_data_types = (
        DecodedsubmitAppealDataArgs,
        DecodedTopUpFeesDataArgs,
        DecodedFinalizeTransactionDataArgs,
    )
    decoded_protocol_call = isinstance(
        getattr(decoded_rollup_transaction, "data", None), protocol_data_types
    )
    decoded_lifecycle_call = isinstance(
        getattr(decoded_rollup_transaction, "data", None), lifecycle_data_types
    )
    consensus_address = consensus_service.public_consensus_main_address()
    envelope_destination = getattr(decoded_rollup_transaction, "to_address", None)
    unknown_consensus_selector = False
    if decoded_protocol_call:
        # Calldata only has protocol meaning when the EVM envelope targets
        # ConsensusMain. Previously Studio decoded the selector regardless of
        # ``to``, so the same signed transaction that was a harmless transfer
        # (or a revert) on Consensus could create/finalize/appeal in Studio.
        if (
            not consensus_address
            or not envelope_destination
            or str(envelope_destination).lower() != str(consensus_address).lower()
        ):
            raise InvalidTransactionError("InvalidConsensusDestination")
    elif (
        envelope_destination
        and consensus_address
        and str(envelope_destination).lower() == str(consensus_address).lower()
        and getattr(decoded_rollup_transaction, "raw_data", None)
    ):
        # ConsensusMain has no catch-all protocol entry point. Do not reinterpret
        # an unknown selector as a plain Studio value transfer. It remains a
        # valid signed EVM envelope, though, so classify the failure after nonce
        # admission as a mined execution revert.
        unknown_consensus_selector = True
    elif envelope_destination is None:
        # Raw EVM bytecode deployment is outside Studio's transaction model;
        # intelligent-contract deployment must use deploySalted/addTransaction.
        raise InvalidTransactionError("UnsupportedEvmDeployment")

    from_address = decoded_rollup_transaction.from_address
    value = decoded_rollup_transaction.value
    total_spend = getattr(decoded_rollup_transaction, "total_spend", value)

    if not accounts_manager.is_valid_address(from_address):
        raise InvalidAddressError(
            from_address, f"Invalid address from_address: {from_address}"
        )

    transaction_signature_valid = transactions_parser.transaction_has_valid_signature(
        signed_rollup_transaction, decoded_rollup_transaction
    )
    if not transaction_signature_valid:
        raise InvalidTransactionError("Transaction signature verification failed")

    transaction_hash = consensus_service.generate_transaction_hash(
        signed_rollup_transaction
    )
    try:
        prior_result = transactions_processor.begin_evm_envelope(
            transaction_hash,
            from_address,
            decoded_rollup_transaction.nonce,
        )
    except ValueError as exc:
        raise InvalidTransactionError(str(exc)) from exc
    if prior_result is not None:
        return prior_result

    def execution_revert(reason: Exception) -> NoReturn:
        """Classify a post-admission protocol rejection as a mined revert."""
        raise _EvmExecutionReverted(
            transaction_hash=transaction_hash,
            from_address=from_address,
            to_address=envelope_destination,
            nonce=decoded_rollup_transaction.nonce,
            reason=reason,
        ) from reason

    if unknown_consensus_selector:
        execution_revert(InvalidTransactionError("UnknownConsensusSelector"))

    # A rejected or invalid signed envelope must not leave a new account row.
    if accounts_manager.get_account(from_address) is None:
        accounts_manager.create_new_account_with_address(from_address, commit=False)

    def finish_envelope(result: str) -> str:
        transactions_processor.record_evm_envelope(
            transaction_hash,
            from_address,
            decoded_rollup_transaction.nonce,
            result,
            to_address=envelope_destination,
        )
        commit = getattr(session, "commit", None)
        if callable(commit):
            commit()
        return result

    if decoded_lifecycle_call:
        post_commit_event = None
        try:
            _reject_genvm_executor_selector_unless_deploy(sim_config, is_deploy=False)
            if isinstance(decoded_rollup_transaction.data, DecodedsubmitAppealDataArgs):
                appealed_tx_id = _handle_appeal_or_top_up_and_submit(
                    accounts_manager=accounts_manager,
                    transactions_processor=transactions_processor,
                    msg_handler=msg_handler,
                    decoded_rollup_transaction=decoded_rollup_transaction,
                    emit_event=False,
                )
                post_commit_event = _transaction_appeal_updated_event(appealed_tx_id)
            elif isinstance(decoded_rollup_transaction.data, DecodedTopUpFeesDataArgs):
                _handle_top_up_fees(
                    accounts_manager=accounts_manager,
                    transactions_processor=transactions_processor,
                    decoded_rollup_transaction=decoded_rollup_transaction,
                )
            else:
                assert isinstance(
                    decoded_rollup_transaction.data,
                    DecodedFinalizeTransactionDataArgs,
                )
                _handle_finalize_transaction(
                    transactions_processor=transactions_processor,
                    decoded_rollup_transaction=decoded_rollup_transaction,
                )
        except (InvalidTransactionError, InvalidAddressError, JSONRPCError) as exc:
            raise _EvmExecutionReverted(
                transaction_hash=transaction_hash,
                from_address=from_address,
                to_address=envelope_destination,
                nonce=decoded_rollup_transaction.nonce,
                reason=exc,
            ) from exc
        result = finish_envelope(transaction_hash)
        if post_commit_event is not None:
            try:
                msg_handler.send_message(
                    log_event=post_commit_event,
                    log_to_terminal=False,
                )
            except Exception:
                # The protocol mutation and EVM envelope are already committed.
                # A transient websocket/log transport failure must not turn a
                # mined transaction into an RPC error or invite a duplicate.
                logger.exception("Failed to publish committed appeal event")
        return result
    else:
        try:
            _validate_fee_envelope(decoded_rollup_transaction)
        except InvalidTransactionError as exc:
            # The fee tuple, deployment salt, and submitted value are checked
            # by ConsensusMain after the signed EVM envelope is admitted. A
            # rejection therefore consumes the EVM nonce and has a status-0
            # receipt; it is not an eth_sendRawTransaction preflight error.
            execution_revert(exc)
        # Raw transaction submission is idempotent. Resolve duplicates before
        # reserving a virtual-factory sequence slot or touching balances.
        transactions_processor.lock_transaction_admission(transaction_hash)
        is_duplicate = transactions_processor.get_transaction_by_hash(transaction_hash)
        if is_duplicate is not None:
            return finish_envelope(transaction_hash)
        to_address = decoded_rollup_transaction.to_address
        nonce = decoded_rollup_transaction.nonce
        value = decoded_rollup_transaction.value
        total_spend = getattr(decoded_rollup_transaction, "total_spend", value)
        genlayer_transaction = transactions_parser.get_genlayer_transaction(
            decoded_rollup_transaction
        )
        # CreationPhase treats an ordinary direct caller as authoritative and
        # ignores a spoofed sender embedded in calldata. Bind Studio's durable
        # transaction owner to the recovered signed-envelope sender as well;
        # otherwise value debits and later refunds can name different parties.
        genlayer_transaction.from_address = from_address
        genlayer_transaction.max_rotations = _funded_max_rotations(
            decoded_rollup_transaction,
            genlayer_transaction.max_rotations,
        )
        _reject_genvm_executor_selector_unless_deploy(
            sim_config,
            is_deploy=genlayer_transaction.type == TransactionType.DEPLOY_CONTRACT,
        )

        # Complete every discoverable admission check before reserving storage,
        # a virtual-factory sequence slot, or sender funds.
        storage_reservation = None
        if genlayer_transaction.type == TransactionType.RUN_CONTRACT:
            to_address = genlayer_transaction.to_address
            if not accounts_manager.is_valid_address(to_address):
                raise InvalidAddressError(
                    to_address, f"Invalid address to_address: {to_address}"
                )

            if not transactions_processor.is_genvm_contract_address(to_address):
                execution_revert(InvalidTransactionError("NonGenVMContract"))

            # Size-only lookup: do not hydrate current_state.data (can be
            # tens of MB) just to test existence.
            if live_state_column_size(session, to_address) is None:
                raise NotFoundError(
                    message="Contract not found",
                    data={"address": to_address},
                )

            try:
                _enforce_pending_queue_caps(
                    transactions_processor=transactions_processor,
                    to_address=to_address,
                    from_address=from_address,
                )
            except QueueDepthExceeded as exc:
                execution_revert(exc)
            storage_reservation = enforce_contract_storage_quota(
                session, to_address, transaction_hash
            )
        elif genlayer_transaction.type == TransactionType.DEPLOY_CONTRACT:
            # A successful deployment always creates a fresh recipient (and a
            # reused CREATE2 salt is rejected by the virtual factory), so only the
            # sender cap can be non-zero before the authoritative address is
            # known.
            try:
                _enforce_pending_queue_caps(
                    transactions_processor=transactions_processor,
                    to_address=None,
                    from_address=from_address,
                )
            except QueueDepthExceeded as exc:
                execution_revert(exc)

        transaction_data = {}
        leader_only = False
        execution_mode = "NORMAL"
        if genlayer_transaction.type != TransactionType.SEND:
            leader_only = genlayer_transaction.data.leader_only
            execution_mode = genlayer_transaction.data.execution_mode

        if genlayer_transaction.type == TransactionType.DEPLOY_CONTRACT:
            try:
                new_contract_address = _allocate_top_level_ghost_address(
                    transactions_processor,
                    int(decoded_rollup_transaction.data.args.salt_nonce or 0),
                    from_address,
                )
                # Keep the address row in the transaction admission unit. A
                # premature commit here would release the duplicate-admission
                # and virtual-factory advisory locks before the raw tx exists.
                accounts_manager.create_new_account_with_address(
                    new_contract_address,
                    commit=False,
                )
            except InvalidTransactionError as exc:
                if storage_reservation is not None:
                    storage_reservation.release()
                execution_revert(exc)
            except Exception:
                if storage_reservation is not None:
                    storage_reservation.release()
                raise

            transaction_data = {
                "contract_address": new_contract_address,
                "contract_code": genlayer_transaction.data.contract_code,
                "calldata": genlayer_transaction.data.calldata,
            }
            if fee_metadata := _fee_metadata(decoded_rollup_transaction):
                transaction_data.update(fee_metadata)
            to_address = new_contract_address
        elif genlayer_transaction.type == TransactionType.RUN_CONTRACT:
            # Contract Call
            to_address = genlayer_transaction.to_address
            transaction_data = {"calldata": genlayer_transaction.data.calldata}
            if fee_metadata := _fee_metadata(decoded_rollup_transaction):
                transaction_data.update(fee_metadata)

        try:
            # Debit sender BEFORE insert. Mint on demand if insufficient (Studio sandbox).
            # Skip for SEND (execute_transfer handles it).
            if (
                total_spend > 0
                and from_address
                and genlayer_transaction.type != TransactionType.SEND
            ):
                _sandbox_debit_sender(accounts_manager, from_address, total_spend)

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
                commit=False,
            )
        except Exception:
            if storage_reservation is not None:
                storage_reservation.release()
            raise

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

        return finish_envelope(transaction_hash)


def get_transactions_for_address(
    transactions_processor: TransactionsProcessor,
    accounts_manager: AccountsManager,
    address: str,
    filter: str = TransactionAddressFilter.ALL.value,
) -> list[dict]:
    if not accounts_manager.is_valid_address(address):
        raise InvalidAddressError(address)

    return _sanitize_rpc_private_keys(
        transactions_processor.get_transactions_for_address(
            address, TransactionAddressFilter(filter)
        )
    )


@check_forbidden_method_in_hosted_studio
def set_finality_window_time(consensus: ConsensusAlgorithm, time: int) -> None:
    if consensus is None:
        # Silently ignore when consensus is not initialized
        return
    consensus.set_finality_window_time(time)


def get_finality_window_time(consensus: ConsensusAlgorithm) -> int:
    if consensus is None:
        # Preserve the RPC's numeric contract even while consensus is starting.
        return int(os.environ.get("VITE_FINALITY_WINDOW", "1800"))
    return consensus.finality_window_time


def get_chain_id() -> str:
    return hex(get_simulator_chain_id())


def get_net_version() -> str:
    return str(get_simulator_chain_id())


def get_block_number(transactions_processor: TransactionsProcessor) -> str:
    import time

    return hex(int(time.time()))


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
            raise JSONRPCError(
                code=-32602,
                message=f"Invalid block number format: {block_number}",
            )

    block_details = transactions_processor.get_transactions_for_block(
        block_number_int,
        include_full_tx=full_tx,
        include_contract_snapshot=False,
    )

    if not block_details:
        # Return a synthetic empty block — MetaMask needs valid blocks
        # for balance queries and gas estimation to work
        import time as _time

        block_details = {
            "number": hex(block_number_int),
            "hash": "0x" + "0" * 64,
            "parentHash": "0x" + "0" * 64,
            "sha3Uncles": "0x1dcc4de8dec75d7aab85b567b6ccd41ad312451b948a7413f0a142fd40d49347",
            "nonce": "0x" + "0" * 16,
            "logsBloom": "0x" + "00" * 256,
            "transactionsRoot": "0x" + "0" * 64,
            "stateRoot": "0x" + "0" * 64,
            "receiptsRoot": "0x" + "0" * 64,
            "transactions": [],
            "timestamp": hex(int(_time.time())),
            "miner": "0x" + "0" * 40,
            "difficulty": "0x0",
            "totalDifficulty": "0x0",
            "gasUsed": "0x0",
            "gasLimit": "0x1c9c380",
            "baseFeePerGas": "0x0",
            "size": "0x0",
            "extraData": "0x",
            "mixHash": "0x" + "0" * 64,
            "uncles": [],
        }

    return _sanitize_rpc_private_keys(block_details)


def get_gas_price() -> str:
    gas_price_in_wei = 0
    return hex(gas_price_in_wei)


def get_gas_estimate(data: Any) -> str:
    # Return a reasonable estimate within block gas limit (30M).
    # Gas price is 0 so transactions are still gasless.
    return hex(0x7A120)  # 500,000 — fits within block limit, enough for any Studio tx


def get_transaction_receipt(
    transactions_processor: TransactionsProcessor,
    transaction_hash: str,
) -> dict | None:

    transaction = transactions_processor.get_transaction_by_hash(
        transaction_hash, include_contract_snapshot=False
    )
    envelope = transactions_processor.get_evm_envelope(transaction_hash)
    if not transaction:
        if envelope is None:
            return None
        return {
            "transactionHash": transaction_hash,
            "transactionIndex": hex(0),
            "blockHash": transaction_hash,
            "blockNumber": hex(0),
            "from": envelope.from_address,
            "to": envelope.to_address,
            "cumulativeGasUsed": hex(0),
            "gasUsed": hex(0),
            "effectiveGasPrice": "0x0",
            "type": "0x0",
            "contractAddress": None,
            "logs": [],
            "logsBloom": "0x" + "00" * 256,
            "status": hex(1 if envelope.success else 0),
        }

    protocol_to_addr = envelope.to_address if envelope is not None else None
    to_addr = protocol_to_addr or transaction.get("to_address")
    from_addr = transaction.get("from_address")
    logs = []
    if int(transaction.get("type", TransactionType.SEND.value)) != int(
        TransactionType.SEND.value
    ):
        # CreationPhase always emits CreatedTransaction for a Consensus
        # transaction. It only additionally emits NewTransaction when this was
        # the recipient queue head at admission. Studio does not persist that
        # historical boolean, so expose the universal event instead of
        # fabricating NewTransaction. Plain EVM value transfers are not created
        # through CreationPhase and therefore emit neither event.
        event_signature = "CreatedTransaction(bytes32,uint256)"
        event_signature_hash = eth_utils.keccak(text=event_signature).hex()
        try:
            tx_slot = int(transaction.get("tx_slot", 0))
        except (TypeError, ValueError):
            tx_slot = 0

        logs.append(
            {
                "address": to_addr,
                "topics": [
                    f"0x{event_signature_hash}",
                    transaction_hash,
                ],
                "data": "0x" + tx_slot.to_bytes(32, "big").hex(),
                "blockNumber": 0,
                "transactionHash": transaction_hash,
                "transactionIndex": 0,
                "blockHash": transaction_hash,
                "logIndex": 0,
                "removed": False,
            }
        )

    receipt = {
        "transactionHash": transaction_hash,
        "transactionIndex": hex(0),
        "blockHash": transaction_hash,
        "blockNumber": hex(transaction.get("block_number", 0)),
        "from": from_addr,
        "to": to_addr,
        "cumulativeGasUsed": hex(transaction.get("gas_used", 8000000)),
        "gasUsed": hex(transaction.get("gas_used", 8000000)),
        "effectiveGasPrice": "0x0",
        "type": "0x0",
        # The signed EVM envelope calls ConsensusMain; deploySalted authors a
        # GenVM ghost inside that call and is not EVM contract creation.
        "contractAddress": None,
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

    transaction = transactions_processor.get_transaction_by_hash(
        block_hash, include_contract_snapshot=False
    )

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

    return _sanitize_rpc_private_keys(block_details)


def get_contract(consensus_service: ConsensusService, contract_name: str) -> dict:
    """Deprecated: consensus contract info is now provided by genlayer-js chain config.

    Get contract instance by name.

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
        "address": (
            consensus_service.public_consensus_main_address()
            if contract_name == "ConsensusMain"
            else contract["address"]
        ),
        "abi": contract["abi"],
        "bytecode": contract["bytecode"],
    }


@check_forbidden_method_in_hosted_studio
def create_snapshot(
    snapshot_manager: SnapshotManager,
) -> int:
    """Create a new snapshot of the current state and transactions.

    Returns:
        int: The snapshot ID
    """
    snapshot = snapshot_manager.create_snapshot()
    return snapshot.snapshot_id


@check_forbidden_method_in_hosted_studio
def restore_snapshot(
    snapshot_manager: SnapshotManager,
    snapshot_id: int,
) -> bool:
    """Restore the database state from a snapshot.

    Args:
        snapshot_id: ID of the snapshot to restore

    Returns:
        bool: True if the snapshot was restored, False otherwise
    """
    reverted = snapshot_manager.restore_snapshot(snapshot_id)
    return reverted


@check_forbidden_method_in_hosted_studio
def delete_all_snapshots(
    snapshot_manager: SnapshotManager,
) -> dict:
    """Delete all snapshots from the database.

    Returns:
        dict: Information about the deletion result
    """
    deleted_count = snapshot_manager.delete_all_snapshots()
    return {"deleted_count": deleted_count}


@check_forbidden_method_in_hosted_studio
def update_transaction_status(
    session: Session,
    transaction_hash: str,
    new_status: str,
) -> dict:
    """Update a transaction status using a request-scoped session."""
    # Validate transaction hash format
    if not transaction_hash or not isinstance(transaction_hash, str):
        raise JSONRPCError(
            code=-32602,
            message="Invalid transaction hash: must be a non-empty string",
            data={},
        )

    if not transaction_hash.startswith("0x") or len(transaction_hash) != 66:
        raise JSONRPCError(
            code=-32602,
            message="Invalid transaction hash format: must be a 66-character hex string starting with '0x'",
            data={},
        )

    try:
        int(transaction_hash, 16)
    except ValueError:
        raise JSONRPCError(
            code=-32602,
            message="Invalid transaction hash format: contains non-hexadecimal characters",
            data={},
        )

    # Validate new status is a valid TransactionStatus enum value
    if not new_status or not isinstance(new_status, str):
        raise JSONRPCError(
            code=-32602, message="Invalid status: must be a non-empty string", data={}
        )

    try:
        status_enum = TransactionStatus(new_status)
    except ValueError:
        valid_statuses = [status.value for status in TransactionStatus]
        raise JSONRPCError(
            code=-32602,
            message=f"Invalid status '{new_status}': must be one of {valid_statuses}",
            data={},
        )

    transactions_processor = TransactionsProcessor(session)

    transactions_processor.update_transaction_status(
        transaction_hash=transaction_hash,
        new_status=status_enum,
        update_current_status_changes=True,
    )

    # Return the updated transaction
    updated_transaction = transactions_processor.get_transaction_by_hash(
        transaction_hash
    )
    if updated_transaction is None:
        raise JSONRPCError(
            code=-32602, message=f"Transaction not found: {transaction_hash}", data={}
        )

    return _sanitize_rpc_private_keys(updated_transaction)


def dev_get_pool_status(sqlalchemy_db) -> dict:
    """
    Development endpoint to monitor database connection pool status.

    Returns current pool metrics including size, checked out connections,
    overflow, and maximum allowed connections.

    Args:
        sqlalchemy_db: The Flask-SQLAlchemy database instance

    Returns:
        dict: Pool status information including timestamp and metrics
    """
    from datetime import datetime

    engine = sqlalchemy_db.engine
    pool = engine.pool

    return {
        "timestamp": datetime.now().isoformat(),
        "pool": {
            "size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "max_allowed": pool.size() + pool._max_overflow,
            "total": pool.size() + pool.overflow(),
        },
    }


####### ADMIN API KEY RATE LIMITING ENDPOINTS #######


@require_admin_access
def admin_create_tier(
    session: Session,
    name: str,
    rate_limit_minute: int,
    rate_limit_hour: int,
    rate_limit_day: int,
    admin_key: str = None,
) -> dict:
    from backend.database_handler.models import ApiTier

    tier = ApiTier(
        name=name,
        rate_limit_minute=rate_limit_minute,
        rate_limit_hour=rate_limit_hour,
        rate_limit_day=rate_limit_day,
    )
    session.add(tier)
    session.flush()
    return {"id": tier.id, "name": tier.name}


@require_admin_access
def admin_list_tiers(
    session: Session,
    admin_key: str = None,
) -> list[dict]:
    from backend.database_handler.models import ApiTier

    tiers = session.query(ApiTier).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "rate_limit_minute": t.rate_limit_minute,
            "rate_limit_hour": t.rate_limit_hour,
            "rate_limit_day": t.rate_limit_day,
        }
        for t in tiers
    ]


@require_admin_access
def admin_create_api_key(
    session: Session,
    tier_name: str,
    description: str = None,
    admin_key: str = None,
) -> dict:
    from backend.database_handler.models import ApiKey, ApiTier

    tier = session.query(ApiTier).filter_by(name=tier_name).first()
    if not tier:
        raise JSONRPCError(code=-32602, message=f"Tier not found: {tier_name}")

    raw_key = "glk_" + secrets_module.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = ApiKey(
        key_prefix=raw_key[:8],
        key_hash=key_hash,
        tier_id=tier.id,
        is_active=True,
        description=description,
    )
    session.add(api_key)
    session.flush()
    return {
        "api_key": raw_key,
        "key_prefix": raw_key[:8],
        "tier": tier_name,
        "description": description,
    }


@require_admin_access
async def admin_deactivate_api_key(
    session: Session,
    key_prefix: str,
    rate_limiter: Any = None,
    admin_key: str = None,
) -> dict:
    from backend.database_handler.models import ApiKey

    api_key = (
        session.query(ApiKey).filter_by(key_prefix=key_prefix, is_active=True).first()
    )
    if not api_key:
        raise NotFoundError(
            message=f"Active API key with prefix {key_prefix} not found"
        )

    api_key.is_active = False
    session.flush()

    if rate_limiter:
        await rate_limiter.invalidate_key_cache(api_key.key_hash)

    return {"key_prefix": key_prefix, "deactivated": True}
