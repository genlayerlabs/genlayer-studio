# rpc/endpoints.py
import random
import json
import eth_utils
from functools import partial, wraps
from typing import Any
from flask_jsonrpc import JSONRPC
from flask_jsonrpc.exceptions import JSONRPCError
from sqlalchemy import Table
from sqlalchemy.orm import Session
import backend.validators as validators

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.llm_providers import LLMProviderRegistry
from backend.rollup.consensus_service import ConsensusService
from backend.database_handler.models import Base
from backend.domain.types import LLMProvider, Validator, TransactionType, SimConfig
from backend.node.create_nodes.providers import (
    get_default_provider_for,
    validate_provider,
)
from backend.protocol_rpc.message_handler.base import (
    MessageHandler,
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

from backend.protocol_rpc.endpoint_generator import generate_rpc_endpoint

from backend.protocol_rpc.transactions_parser import TransactionParser
from backend.errors.errors import InvalidAddressError, InvalidTransactionError

from backend.database_handler.transactions_processor import (
    TransactionAddressFilter,
    TransactionsProcessor,
)
from backend.node.base import Node, SIMULATOR_CHAIN_ID
from backend.node.types import ExecutionMode, ExecutionResultStatus
from backend.consensus.base import ConsensusAlgorithm

from flask_jsonrpc.exceptions import JSONRPCError
import base64
import os
from backend.protocol_rpc.message_handler.types import LogEvent, EventType, EventScope
from backend.protocol_rpc.types import DecodedsubmitAppealDataArgs
from backend.database_handler.snapshot_manager import SnapshotManager
import asyncio


####### WRAPPER TO BLOCK ENDPOINTS FOR HOSTED ENVIRONMENT #######
def check_forbidden_method_in_hosted_studio(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if os.getenv("VITE_IS_HOSTED") == "true":
            raise JSONRPCError(
                code=-32000,
                message="Non-allowed operation",
                data={},
            )
        return func(*args, **kwargs)

    return wrapper


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
    accounts_manager: AccountsManager,
    transactions_processor: TransactionsProcessor,
    account_address: str,
    amount: int,
) -> str:
    if not accounts_manager.is_valid_address(account_address):
        raise InvalidAddressError(account_address)

    nonce = transactions_processor.get_transaction_count(None)
    transaction_hash = transactions_processor.insert_transaction(
        None, account_address, None, amount, 0, nonce, False, 0
    )
    return transaction_hash


@check_forbidden_method_in_hosted_studio
def reset_defaults_llm_providers(llm_provider_registry: LLMProviderRegistry) -> None:
    llm_provider_registry.reset_defaults()


async def check_provider_is_available(
    validators_manager: validators.Manager, provider: LLMProvider | dict
) -> bool:
    return await validators_manager.llm_module.provider_available(
        provider.model if isinstance(provider, LLMProvider) else provider["model"],
        (
            provider.plugin_config["api_url"]
            if isinstance(provider, LLMProvider)
            else provider["plugin_config"]["api_url"]
        ),
        provider.plugin if isinstance(provider, LLMProvider) else provider["plugin"],
        (
            provider.plugin_config["api_key_env_var"]
            if isinstance(provider, LLMProvider)
            else provider["plugin_config"]["api_key_env_var"]
        ),
    )


async def get_providers_and_models(
    llm_provider_registry: LLMProviderRegistry,
    validators_manager: validators.Manager,
) -> list[dict]:
    providers = await llm_provider_registry.get_all_dict()
    sem = asyncio.Semaphore(8)

    async def check_with_semaphore(validators_manager, provider):
        async with sem:
            return await check_provider_is_available(validators_manager, provider)

    availability = await asyncio.gather(
        *(check_with_semaphore(validators_manager, p) for p in providers)
    )
    for provider, is_available in zip(providers, availability):
        provider["is_model_available"] = is_available
    return providers


@check_forbidden_method_in_hosted_studio
def add_provider(llm_provider_registry: LLMProviderRegistry, params: dict) -> int:
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
def update_provider(
    llm_provider_registry: LLMProviderRegistry, id: int, params: dict
) -> None:
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
def delete_provider(llm_provider_registry: LLMProviderRegistry, id: int) -> None:
    llm_provider_registry.delete(id)


async def create_validator(
    validators_registry: ModifiableValidatorsRegistry,
    accounts_manager: AccountsManager,
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

    account = accounts_manager.create_new_account()

    return await validators_registry.create_validator(
        Validator(
            address=account.address,
            private_key=account.key,
            stake=stake,
            llmprovider=llm_provider,
        )
    )


@check_forbidden_method_in_hosted_studio
async def create_random_validator(
    validators_registry: ModifiableValidatorsRegistry,
    accounts_manager: AccountsManager,
    llm_provider_registry: LLMProviderRegistry,
    validators_manager: validators.Manager,
    stake: int,
) -> dict:
    return (
        await create_random_validators(
            validators_registry,
            accounts_manager,
            llm_provider_registry,
            validators_manager,
            1,
            stake,
            stake,
        )
    )[0]


@check_forbidden_method_in_hosted_studio
async def create_random_validators(
    validators_registry: ModifiableValidatorsRegistry,
    accounts_manager: AccountsManager,
    llm_provider_registry: LLMProviderRegistry,
    validators_manager: validators.Manager,
    count: int,
    min_stake: int,
    max_stake: int,
    limit_providers: list[str] = None,
    limit_models: list[str] = None,
) -> list[dict]:
    limit_providers = limit_providers or []
    limit_models = limit_models or []

    details = await random_validator_config(
        llm_provider_registry.get_all,
        partial(check_provider_is_available, validators_manager),
        limit_providers=set(limit_providers),
        limit_models=set(limit_models),
        amount=count,
    )

    response = []
    for detail in details:
        stake = random.randint(min_stake, max_stake)
        validator_account = accounts_manager.create_new_account()

        validator = await validators_registry.create_validator(
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
    validators_registry: ModifiableValidatorsRegistry,
    accounts_manager: AccountsManager,
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
    return await validators_registry.update_validator(validator)


@check_forbidden_method_in_hosted_studio
async def delete_validator(
    validators_registry: ModifiableValidatorsRegistry,
    accounts_manager: AccountsManager,
    validator_address: str,
) -> str:
    # Remove validation while adding migration to update the db address
    # if not accounts_manager.is_valid_address(validator_address):
    #     raise InvalidAddressError(validator_address)

    await validators_registry.delete_validator(validator_address)
    return validator_address


@check_forbidden_method_in_hosted_studio
async def delete_all_validators(
    validators_registry: ModifiableValidatorsRegistry,
) -> list:
    await validators_registry.delete_all_validators()
    return validators_registry.get_all_validators()


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
    if not accounts_manager.is_valid_address(contract_address):
        raise InvalidAddressError(
            contract_address,
            "Incorrect address format. Please provide a valid address.",
        )
    contract_account = accounts_manager.get_account_or_fail(contract_address)

    if not contract_account["data"] or not contract_account["data"]["code"]:
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
    )
    schema = await node.get_contract_schema(
        base64.b64decode(contract_account["data"]["code"])
    )
    return json.loads(schema)


async def get_contract_schema_for_code(
    msg_handler: MessageHandler, contract_code_hex: str
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
    )
    schema = await node.get_contract_schema(
        eth_utils.hexadecimal.decode_hex(contract_code_hex)
    )
    return json.loads(schema)


async def _execute_call_with_snapshot(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: MessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
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
            virtual_validators.append(Validator(
                address=account.address,
                private_key=account.key,
                stake=validator.stake,
                llmprovider=llm_provider,
            ))
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
            virtual_validators.append(Validator(
                address=account.address,
                private_key=account.key,
                stake=0,
                llmprovider=llm_provider,
            ))
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
            msg_handler,
            transactions_parser,
            snapshot,
            params,
        )
        return receipt


async def gen_call(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: MessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    params: dict,
) -> str:
    receipt = await _execute_call_with_snapshot(
        session,
        accounts_manager,
        msg_handler,
        transactions_parser,
        validators_manager,
        params,
    )
    return eth_utils.hexadecimal.encode_hex(receipt.result[1:])[2:]


async def sim_call(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: MessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    params: dict,
) -> dict:
    receipt = await _execute_call_with_snapshot(
        session,
        accounts_manager,
        msg_handler,
        transactions_parser,
        validators_manager,
        params,
    )
    return receipt.to_dict()


async def _gen_call_with_validator(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: MessageHandler,
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
    )

    if type == "read":
        decoded_data = transactions_parser.decode_method_call_data(data)
        receipt = await node.get_contract_data(
            from_address=from_address,
            calldata=decoded_data.calldata,
            state_status=state_status,
        )
    elif type == "write":
        decoded_data = transactions_parser.decode_method_send_data(data)
        receipt = await node.run_contract(
            from_address=from_address,
            calldata=decoded_data.calldata,
        )
    elif type == "deploy":
        decoded_data = transactions_parser.decode_deployment_data(data)
        receipt = await node.deploy_contract(
            from_address=from_address,
            code_to_deploy=decoded_data.contract_code,
            calldata=decoded_data.calldata,
        )
    else:
        raise JSONRPCError(f"Invalid type: {type}")

    # Return the result of the write method
    if receipt.execution_result != ExecutionResultStatus.SUCCESS:
        raise JSONRPCError(
            message="running contract failed", data={"receipt": receipt.to_dict()}
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
    transactions_processor: TransactionsProcessor, transaction_hash: str
) -> dict | None:
    return transactions_processor.get_transaction_by_hash(transaction_hash)


async def eth_call(
    session: Session,
    accounts_manager: AccountsManager,
    msg_handler: MessageHandler,
    transactions_parser: TransactionParser,
    validators_manager: validators.Manager,
    params: dict,
    block_tag: str = "latest",
) -> str:
    to_address = params["to"]
    from_address = params["from"] if "from" in params else None
    data = params["data"]

    if from_address is None:
        return base64.b64encode(b"\x00' * 31 + b'\x01").decode(
            "ascii"
        )  # Return '1' as a uint256

    if from_address and not accounts_manager.is_valid_address(from_address):
        raise InvalidAddressError(from_address)

    if not accounts_manager.is_valid_address(to_address):
        raise InvalidAddressError(to_address)

    decoded_data = transactions_parser.decode_method_call_data(data)

    async with validators_manager.snapshot() as snapshot:
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
    transactions_processor: TransactionsProcessor,
    msg_handler: MessageHandler,
    accounts_manager: AccountsManager,
    transactions_parser: TransactionParser,
    consensus_service: ConsensusService,
    signed_rollup_transaction: str,
    sim_config: dict | None = None,
) -> str:
    # Decode transaction
    decoded_rollup_transaction = transactions_parser.decode_signed_transaction(
        signed_rollup_transaction
    )
    # Debug logging - only in DEBUG mode
    if os.environ.get("LOG_LEVEL", "INFO").upper() == "DEBUG":
        print("DECODED ROLLUP TRANSACTION", decoded_rollup_transaction)

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
        to_address = decoded_rollup_transaction.to_address
        nonce = decoded_rollup_transaction.nonce
        value = decoded_rollup_transaction.value
        genlayer_transaction = transactions_parser.get_genlayer_transaction(
            decoded_rollup_transaction
        )

        transaction_data = {}
        leader_only = False
        rollup_transaction_details = None
        if genlayer_transaction.type != TransactionType.SEND:
            leader_only = genlayer_transaction.data.leader_only
            rollup_transaction_details = consensus_service.add_transaction(
                signed_rollup_transaction, from_address
            )  # because hardhat accounts are not funded

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
                raise JSONRPCError(f"Contract address does not exist: {to_address}")

            transaction_data = {"calldata": genlayer_transaction.data.calldata}

        # Obtain transaction hash from new transaction event
        if rollup_transaction_details and "tx_id_hex" in rollup_transaction_details:
            transaction_hash = rollup_transaction_details["tx_id_hex"]
        else:
            transaction_hash = None

        # Insert transaction into the database
        transaction_hash = transactions_processor.insert_transaction(
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
    consensus.set_finality_window_time(time)


def get_finality_window_time(consensus: ConsensusAlgorithm) -> int:
    return consensus.finality_window_time


def get_chain_id() -> str:
    return hex(SIMULATOR_CHAIN_ID)


def get_net_version() -> str:
    return str(SIMULATOR_CHAIN_ID)


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
        raise JSONRPCError(f"Block not found for number: {block_number}")

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
        "cumulativeGasUsed": hex(transaction.get("gas_used", 21000)),
        "gasUsed": hex(transaction.get("gas_used", 21000)),
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
    transaction_hash: str,
    full_tx: bool = False,
) -> dict | None:

    transaction = transactions_processor.get_transaction_by_hash(transaction_hash)

    if not transaction:
        return None

    block_details = {
        "hash": transaction_hash,
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
        "gasUsed": hex(transaction.get("gas_used", 21000)),
        "logsBloom": "0x" + "00" * 256,
        "transactions": [],
    }

    if full_tx:
        block_details["transactions"].append(transaction)
    else:
        block_details["transactions"].append(transaction_hash)

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
        raise JSONRPCError(
            message=f"Contract {contract_name} not found",
            data={"contract_name": contract_name},
        )

    return {
        "address": contract["address"],
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


def register_all_rpc_endpoints(
    jsonrpc: JSONRPC,
    msg_handler: MessageHandler,
    request_session: Session,
    accounts_manager: AccountsManager,
    snapshot_manager: SnapshotManager,
    transactions_processor: TransactionsProcessor,
    validators_registry: ModifiableValidatorsRegistry,
    validators_manager: validators.Manager,
    llm_provider_registry: LLMProviderRegistry,
    consensus: ConsensusAlgorithm,
    consensus_service: ConsensusService,
    transactions_parser: TransactionParser,
    sqlalchemy_db=None,  # Optional for backward compatibility
):
    register_rpc_endpoint = partial(generate_rpc_endpoint, jsonrpc, msg_handler)

    register_rpc_endpoint(ping)
    register_rpc_endpoint(
        partial(clear_db_tables, request_session),
        method_name="sim_clearDbTables",
    )
    register_rpc_endpoint(
        partial(fund_account, accounts_manager, transactions_processor),
        method_name="sim_fundAccount",
    )
    register_rpc_endpoint(
        partial(get_providers_and_models, llm_provider_registry, validators_manager),
        method_name="sim_getProvidersAndModels",
    )
    register_rpc_endpoint(
        partial(reset_defaults_llm_providers, llm_provider_registry),
        method_name="sim_resetDefaultsLlmProviders",
    )
    register_rpc_endpoint(
        partial(add_provider, llm_provider_registry),
        method_name="sim_addProvider",
    )
    register_rpc_endpoint(
        partial(update_provider, llm_provider_registry),
        method_name="sim_updateProvider",
    )
    register_rpc_endpoint(
        partial(delete_provider, llm_provider_registry),
        method_name="sim_deleteProvider",
    )
    register_rpc_endpoint(
        partial(
            check_forbidden_method_in_hosted_studio(create_validator),
            validators_registry,
            accounts_manager,
        ),
        method_name="sim_createValidator",
    )
    register_rpc_endpoint(
        partial(
            create_random_validator,
            validators_registry,
            accounts_manager,
            llm_provider_registry,
            validators_manager,
        ),
        method_name="sim_createRandomValidator",
    )
    register_rpc_endpoint(
        partial(
            create_random_validators,
            validators_registry,
            accounts_manager,
            llm_provider_registry,
            validators_manager,
        ),
        method_name="sim_createRandomValidators",
    )
    register_rpc_endpoint(
        partial(update_validator, validators_registry, accounts_manager),
        method_name="sim_updateValidator",
    )
    register_rpc_endpoint(
        partial(delete_validator, validators_registry, accounts_manager),
        method_name="sim_deleteValidator",
    )
    register_rpc_endpoint(
        partial(delete_all_validators, validators_registry),
        method_name="sim_deleteAllValidators",
    )
    register_rpc_endpoint(
        partial(get_all_validators, validators_registry),
        method_name="sim_getAllValidators",
    )
    register_rpc_endpoint(
        partial(get_validator, validators_registry),
        method_name="sim_getValidator",
    )
    register_rpc_endpoint(
        partial(count_validators, validators_registry),
        method_name="sim_countValidators",
    )
    register_rpc_endpoint(
        partial(get_contract_schema, accounts_manager, msg_handler),
        method_name="gen_getContractSchema",
    )
    register_rpc_endpoint(
        partial(get_contract_schema_for_code, msg_handler),
        method_name="gen_getContractSchemaForCode",
    )
    register_rpc_endpoint(
        partial(
            gen_call,
            request_session,
            accounts_manager,
            msg_handler,
            transactions_parser,
            validators_manager,
        ),
        method_name="gen_call",
    )
    register_rpc_endpoint(
        partial(
            sim_call,
            request_session,
            accounts_manager,
            msg_handler,
            transactions_parser,
            validators_manager,
        ),
        method_name="sim_call",
    )
    register_rpc_endpoint(
        partial(get_balance, accounts_manager),
        method_name="eth_getBalance",
    )
    register_rpc_endpoint(
        partial(get_transaction_by_hash, transactions_processor),
        method_name="eth_getTransactionByHash",
    )
    register_rpc_endpoint(
        partial(
            eth_call,
            request_session,
            accounts_manager,
            msg_handler,
            transactions_parser,
            validators_manager,
        ),
        method_name="eth_call",
    )
    register_rpc_endpoint(
        partial(
            send_raw_transaction,
            transactions_processor,
            msg_handler,
            accounts_manager,
            transactions_parser,
            consensus_service,
        ),
        method_name="eth_sendRawTransaction",
    )
    register_rpc_endpoint(
        partial(get_transaction_count, transactions_processor),
        method_name="eth_getTransactionCount",
    )
    register_rpc_endpoint(
        partial(get_transactions_for_address, transactions_processor, accounts_manager),
        method_name="sim_getTransactionsForAddress",
    )
    register_rpc_endpoint(
        partial(set_finality_window_time, consensus),
        method_name="sim_setFinalityWindowTime",
    )
    register_rpc_endpoint(
        partial(get_finality_window_time, consensus),
        method_name="sim_getFinalityWindowTime",
    )
    register_rpc_endpoint(
        partial(get_contract, consensus_service),
        method_name="sim_getConsensusContract",
    )
    register_rpc_endpoint(get_chain_id, method_name="eth_chainId")
    register_rpc_endpoint(get_net_version, method_name="net_version")
    register_rpc_endpoint(
        partial(get_block_number, transactions_processor),
        method_name="eth_blockNumber",
    )
    register_rpc_endpoint(
        partial(get_block_by_number, transactions_processor),
        method_name="eth_getBlockByNumber",
    )
    register_rpc_endpoint(get_gas_price, method_name="eth_gasPrice")
    register_rpc_endpoint(get_gas_estimate, method_name="eth_estimateGas")
    register_rpc_endpoint(
        partial(get_transaction_receipt, transactions_processor),
        method_name="eth_getTransactionReceipt",
    )
    register_rpc_endpoint(
        partial(get_block_by_hash, transactions_processor),
        method_name="eth_getBlockByHash",
    )
    register_rpc_endpoint(
        partial(create_snapshot, snapshot_manager),
        method_name="sim_createSnapshot",
    )
    register_rpc_endpoint(
        partial(restore_snapshot, snapshot_manager),
        method_name="sim_restoreSnapshot",
    )
    register_rpc_endpoint(
        partial(delete_all_snapshots, snapshot_manager),
        method_name="sim_deleteAllSnapshots",
    )
    
    register_rpc_endpoint(
        partial(dev_get_pool_status, sqlalchemy_db),
        method_name="dev_getPoolStatus",
    )
