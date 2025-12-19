"""RPC endpoint registrations using FastAPI dependency injection."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc import endpoints as impl
from backend.protocol_rpc.dependencies import (
    get_accounts_manager,
    get_consensus,
    get_consensus_service,
    get_db_session,
    get_llm_provider_registry,
    get_message_handler,
    get_snapshot_manager,
    get_sqlalchemy_db,
    get_transactions_parser,
    get_transactions_processor,
    get_validators_manager,
    get_genvm_manager,
    get_validators_registry,
)
from backend.protocol_rpc.rpc_decorators import rpc


# ---------------------------------------------------------------------------
# Simulator endpoints
# ---------------------------------------------------------------------------


@rpc.method("ping")
def ping() -> str:
    return impl.ping()


@rpc.method(name="sim_clearDbTables", description="Clear all tables in the database")
def clear_db_tables(
    tables: list,
    session: Session = Depends(get_db_session),
) -> None:
    return impl.clear_db_tables(session=session, tables=tables)


@rpc.method("sim_fundAccount")
def fund_account(
    account_address: str,
    amount: int,
    session: Session = Depends(get_db_session),
) -> str:
    return impl.fund_account(
        session=session, account_address=account_address, amount=amount
    )


@rpc.method("sim_getProvidersAndModels")
async def get_providers_and_models(
    genvm_manager=Depends(get_genvm_manager),
    llm_provider_registry=Depends(get_llm_provider_registry),
) -> list[dict]:
    return await impl.get_providers_and_models(
        llm_provider_registry=llm_provider_registry,
        genvm_manager=genvm_manager,
    )


@rpc.method("sim_resetDefaultsLlmProviders")
def reset_defaults_llm_providers(
    llm_provider_registry=Depends(get_llm_provider_registry),
) -> None:
    return impl.reset_defaults_llm_providers(
        llm_provider_registry=llm_provider_registry
    )


@rpc.method("sim_addProvider")
def add_provider(
    params: dict,
    session: Session = Depends(get_db_session),
) -> int:
    return impl.add_provider(session=session, params=params)


@rpc.method("sim_updateProvider")
def update_provider(
    id: int,
    params: dict,
    session: Session = Depends(get_db_session),
) -> None:
    return impl.update_provider(session=session, id=id, params=params)


@rpc.method("sim_deleteProvider")
def delete_provider(
    id: int,
    session: Session = Depends(get_db_session),
) -> None:
    return impl.delete_provider(session=session, id=id)


@rpc.method("sim_createValidator")
async def create_validator(
    stake: int,
    provider: str,
    model: str,
    config: dict | None = None,
    plugin: str | None = None,
    plugin_config: dict | None = None,
    session: Session = Depends(get_db_session),
    validators_manager=Depends(get_validators_manager),
) -> dict:
    return await impl.create_validator(
        session=session,
        validators_manager=validators_manager,
        stake=stake,
        provider=provider,
        model=model,
        config=config,
        plugin=plugin,
        plugin_config=plugin_config,
    )


@rpc.method("sim_createRandomValidator")
async def create_random_validator(
    stake: int,
    session: Session = Depends(get_db_session),
    validators_manager=Depends(get_validators_manager),
    genvm_manager=Depends(get_genvm_manager),
) -> dict:
    return await impl.create_random_validator(
        session=session,
        validators_manager=validators_manager,
        genvm_manager=genvm_manager,
        stake=stake,
    )


@rpc.method("sim_createRandomValidators")
async def create_random_validators(
    count: int,
    min_stake: int,
    max_stake: int,
    limit_providers: list[str] | None = None,
    limit_models: list[str] | None = None,
    session: Session = Depends(get_db_session),
    validators_manager=Depends(get_validators_manager),
    genvm_manager=Depends(get_genvm_manager),
) -> list[dict]:
    return await impl.create_random_validators(
        session=session,
        validators_manager=validators_manager,
        genvm_manager=genvm_manager,
        count=count,
        min_stake=min_stake,
        max_stake=max_stake,
        limit_providers=limit_providers,
        limit_models=limit_models,
    )


@rpc.method("sim_updateValidator")
async def update_validator(
    validator_address: str,
    stake: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    plugin: str | None = None,
    plugin_config: dict | None = None,
    session: Session = Depends(get_db_session),
    validators_manager=Depends(get_validators_manager),
) -> dict:
    return await impl.update_validator(
        session=session,
        validators_manager=validators_manager,
        validator_address=validator_address,
        stake=stake,
        provider=provider,
        model=model,
        plugin=plugin,
        plugin_config=plugin_config,
    )


@rpc.method("sim_deleteValidator")
async def delete_validator(
    validator_address: str,
    validators_manager=Depends(get_validators_manager),
) -> str:
    return await impl.delete_validator(
        validators_manager=validators_manager,
        validator_address=validator_address,
    )


@rpc.method("sim_deleteAllValidators")
async def delete_all_validators(
    validators_manager=Depends(get_validators_manager),
) -> list[dict]:
    return await impl.delete_all_validators(validators_manager=validators_manager)


@rpc.method("sim_getAllValidators")
def get_all_validators(
    validators_registry=Depends(get_validators_registry),
) -> list:
    return impl.get_all_validators(validators_registry=validators_registry)


@rpc.method("sim_getValidator")
def get_validator(
    validator_address: str,
    validators_registry=Depends(get_validators_registry),
) -> dict:
    return impl.get_validator(
        validators_registry=validators_registry,
        validator_address=validator_address,
    )


@rpc.method("sim_countValidators")
def count_validators(
    validators_registry=Depends(get_validators_registry),
) -> int:
    return impl.count_validators(validators_registry=validators_registry)


@rpc.method("sim_upgradeContractCode")
def upgrade_contract_code(
    contract_address: str,
    new_code: str,
    signature: str = None,
    admin_key: str = None,
    session: Session = Depends(get_db_session),
) -> dict:
    return impl.admin_upgrade_contract_code(
        session=session,
        contract_address=contract_address,
        new_code=new_code,
        signature=signature,
        admin_key=admin_key,
    )


@rpc.method("sim_getTransactionsForAddress")
def get_transactions_for_address(
    address: str,
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
    accounts_manager: AccountsManager = Depends(get_accounts_manager),
) -> list[dict]:
    return impl.get_transactions_for_address(
        transactions_processor=transactions_processor,
        accounts_manager=accounts_manager,
        address=address,
    )


@rpc.method("sim_setFinalityWindowTime")
def set_finality_window_time(
    seconds: int,
    consensus=Depends(get_consensus),
) -> dict:
    return impl.set_finality_window_time(consensus, seconds)


@rpc.method("sim_getFinalityWindowTime")
def get_finality_window_time(
    consensus=Depends(get_consensus),
) -> dict:
    return impl.get_finality_window_time(consensus)


@rpc.method("sim_getConsensusContract")
def get_consensus_contract(
    contract_name: str,
    consensus_service=Depends(get_consensus_service),
) -> dict:
    return impl.get_contract(consensus_service, contract_name)


@rpc.method("sim_createSnapshot")
def create_snapshot(
    snapshot_manager=Depends(get_snapshot_manager),
) -> int:
    return impl.create_snapshot(snapshot_manager=snapshot_manager)


@rpc.method("sim_restoreSnapshot")
def restore_snapshot(
    snapshot_id: int,
    snapshot_manager=Depends(get_snapshot_manager),
) -> bool:
    return impl.restore_snapshot(
        snapshot_manager=snapshot_manager, snapshot_id=snapshot_id
    )


@rpc.method("sim_deleteAllSnapshots")
def delete_all_snapshots(
    snapshot_manager=Depends(get_snapshot_manager),
) -> dict:
    return impl.delete_all_snapshots(snapshot_manager=snapshot_manager)


@rpc.method("sim_lintContract")
def lint_contract(
    source_code: str,
    filename: str = "contract.py",
) -> dict:
    return impl.sim_lint_contract(source_code=source_code, filename=filename)


# ---------------------------------------------------------------------------
# GenLayer endpoints
# ---------------------------------------------------------------------------


@rpc.method("gen_getContractSchema")
async def get_contract_schema(
    contract_address: str,
    accounts_manager: AccountsManager = Depends(get_accounts_manager),
    msg_handler=Depends(get_message_handler),
    genvm_manager=Depends(get_genvm_manager),
) -> dict:
    return await impl.get_contract_schema(
        accounts_manager=accounts_manager,
        genvm_manager=genvm_manager,
        msg_handler=msg_handler,
        contract_address=contract_address,
    )


@rpc.method("gen_getContractSchemaForCode")
async def get_contract_schema_for_code(
    contract_code_hex: str,
    msg_handler=Depends(get_message_handler),
    genvm_manager=Depends(get_genvm_manager),
) -> dict:
    return await impl.get_contract_schema_for_code(
        genvm_manager=genvm_manager,
        msg_handler=msg_handler,
        contract_code_hex=contract_code_hex,
    )


@rpc.method("gen_getContractCode")
def get_contract_code(
    contract_address: str,
    session: Session = Depends(get_db_session),
) -> dict:
    return impl.get_contract_code(session=session, contract_address=contract_address)


@rpc.method("gen_getContractNonce")
def get_contract_nonce(
    contract_address: str,
    session: Session = Depends(get_db_session),
) -> int:
    """Get contract nonce (tx count TO contract) for upgrade signatures."""
    return impl.get_contract_nonce(session=session, contract_address=contract_address)


@rpc.method("gen_call")
async def gen_call(
    params: dict,
    session: Session = Depends(get_db_session),
    accounts_manager: AccountsManager = Depends(get_accounts_manager),
    msg_handler=Depends(get_message_handler),
    transactions_parser=Depends(get_transactions_parser),
    validators_manager=Depends(get_validators_manager),
    genvm_manager=Depends(get_genvm_manager),
) -> str:
    return await impl.gen_call(
        session=session,
        accounts_manager=accounts_manager,
        msg_handler=msg_handler,
        transactions_parser=transactions_parser,
        validators_manager=validators_manager,
        genvm_manager=genvm_manager,
        params=params,
    )


@rpc.method("sim_call")
async def sim_call(
    params: dict,
    session: Session = Depends(get_db_session),
    accounts_manager: AccountsManager = Depends(get_accounts_manager),
    msg_handler=Depends(get_message_handler),
    transactions_parser=Depends(get_transactions_parser),
    validators_manager=Depends(get_validators_manager),
    genvm_manager=Depends(get_genvm_manager),
) -> dict:
    return await impl.sim_call(
        session=session,
        accounts_manager=accounts_manager,
        msg_handler=msg_handler,
        transactions_parser=transactions_parser,
        validators_manager=validators_manager,
        genvm_manager=genvm_manager,
        params=params,
    )


# ---------------------------------------------------------------------------
# Ethereum-compatible endpoints
# ---------------------------------------------------------------------------


@rpc.method("eth_getBalance")
def eth_get_balance(
    account_address: str,
    accounts_manager: AccountsManager = Depends(get_accounts_manager),
    block_tag: str = "latest",
) -> int:
    return impl.get_balance(
        accounts_manager=accounts_manager,
        account_address=account_address,
        block_tag=block_tag,
    )


@rpc.method("eth_getTransactionByHash")
def eth_get_transaction_by_hash(
    transaction_hash: str,
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
    sim_config: dict | None = None,
) -> dict:
    return impl.get_transaction_by_hash(
        transactions_processor=transactions_processor,
        transaction_hash=transaction_hash,
        sim_config=sim_config,
    )


@rpc.method("gen_getStudioTransactionByHash")
def get_studio_transaction_by_hash(
    transaction_hash: str,
    full: bool = True,
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
) -> dict:
    return impl.get_studio_transaction_by_hash(
        transactions_processor=transactions_processor,
        transaction_hash=transaction_hash,
        full=full,
    )


@rpc.method("gen_getTransactionStatus")
def get_transaction_status(
    transaction_hash: str,
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
) -> str:
    return impl.get_transaction_status(
        transactions_processor=transactions_processor,
        transaction_hash=transaction_hash,
    )


@rpc.method("eth_call")
async def eth_call(
    params: dict,
    session: Session = Depends(get_db_session),
    accounts_manager: AccountsManager = Depends(get_accounts_manager),
    msg_handler=Depends(get_message_handler),
    transactions_parser=Depends(get_transactions_parser),
    validators_manager=Depends(get_validators_manager),
    genvm_manager=Depends(get_genvm_manager),
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
    block_tag: str = "latest",
) -> str:
    return await impl.eth_call(
        session=session,
        accounts_manager=accounts_manager,
        msg_handler=msg_handler,
        transactions_parser=transactions_parser,
        validators_manager=validators_manager,
        genvm_manager=genvm_manager,
        transactions_processor=transactions_processor,
        params=params,
        block_tag=block_tag,
    )


@rpc.method("eth_sendRawTransaction")
def eth_send_raw_transaction(
    signed_rollup_transaction: str,
    session: Session = Depends(get_db_session),
    msg_handler=Depends(get_message_handler),
    transactions_parser=Depends(get_transactions_parser),
    consensus_service=Depends(get_consensus_service),
    sim_config: dict | None = None,
) -> str:
    return impl.send_raw_transaction(
        session=session,
        msg_handler=msg_handler,
        transactions_parser=transactions_parser,
        consensus_service=consensus_service,
        signed_rollup_transaction=signed_rollup_transaction,
        sim_config=sim_config,
    )


@rpc.method("eth_getTransactionCount")
def eth_get_transaction_count(
    address: str,
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
    block: str = "latest",
) -> str:
    return impl.get_transaction_count(
        transactions_processor=transactions_processor,
        address=address,
        block=block,
    )


@rpc.method("eth_chainId")
def eth_chain_id() -> str:
    return impl.get_chain_id()


@rpc.method("net_version")
def net_version() -> str:
    return impl.get_net_version()


@rpc.method("eth_blockNumber")
def eth_block_number(
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
) -> str:
    return impl.get_block_number(transactions_processor)


@rpc.method("eth_getBlockByNumber")
def eth_get_block_by_number(
    block_number: str,
    full_transactions: bool,
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
) -> dict:
    return impl.get_block_by_number(
        transactions_processor=transactions_processor,
        block_number=block_number,
        full_tx=full_transactions,
    )


@rpc.method("eth_gasPrice")
def eth_gas_price() -> str:
    return impl.get_gas_price()


@rpc.method("eth_estimateGas")
def eth_estimate_gas(
    transaction: dict,
) -> str:
    return impl.get_gas_estimate(transaction)


@rpc.method("eth_getTransactionReceipt")
def eth_get_transaction_receipt(
    transaction_hash: str,
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
) -> dict | None:
    return impl.get_transaction_receipt(
        transactions_processor=transactions_processor,
        transaction_hash=transaction_hash,
    )


@rpc.method("eth_getBlockByHash")
def eth_get_block_by_hash(
    block_hash: str,
    full_transactions: bool,
    transactions_processor: TransactionsProcessor = Depends(get_transactions_processor),
) -> dict | None:
    return impl.get_block_by_hash(
        transactions_processor=transactions_processor,
        block_hash=block_hash,
        full_tx=full_transactions,
    )


@rpc.method("sim_updateTransactionStatus")
def sim_update_transaction_status(
    transaction_hash: str,
    new_status: str,
    session: Session = Depends(get_db_session),
) -> dict:
    return impl.update_transaction_status(
        session=session,
        transaction_hash=transaction_hash,
        new_status=new_status,
    )


# ---------------------------------------------------------------------------
# Developer endpoints
# ---------------------------------------------------------------------------


@rpc.method("dev_getPoolStatus")
def dev_get_pool_status(
    sqlalchemy_db=Depends(get_sqlalchemy_db),
) -> dict:
    return impl.dev_get_pool_status(sqlalchemy_db)
