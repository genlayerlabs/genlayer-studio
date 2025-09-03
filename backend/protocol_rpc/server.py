# backend/protocol_rpc/server.py

import os
from os import environ
import threading
from flask import Flask
from flask_jsonrpc.app import JSONRPC
from flask_socketio import SocketIO, join_room, leave_room
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.database_handler.llm_providers import LLMProviderRegistry
from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.protocol_rpc.endpoints import register_all_rpc_endpoints
from backend.protocol_rpc.endpoint_generator import setup_eth_method_handler
from backend.protocol_rpc.validators_init import initialize_validators
from backend.protocol_rpc.transactions_parser import TransactionParser
from dotenv import load_dotenv
import backend.validators as validators
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.validators_registry import (
    ValidatorsRegistry,
    ModifiableValidatorsRegistry,
)
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.snapshot_manager import SnapshotManager
from backend.database_handler.session_manager import managed_session
from backend.consensus.base import ConsensusAlgorithm, contract_processor_factory
from backend.database_handler.models import Base, TransactionStatus
from backend.rollup.consensus_service import ConsensusService
from backend.protocol_rpc.aio import MAIN_SERVER_LOOP, MAIN_LOOP_EXITING, MAIN_LOOP_DONE
from backend.domain.types import TransactionType
from typing import cast


def get_db_name(database: str) -> str:
    return "genlayer_state" if database == "genlayer" else database


async def create_app():
    # Set up unified logging BEFORE any other components
    from backend.protocol_rpc.message_handler.base import setup_loguru_config

    setup_loguru_config()

    def create_session():
        return Session(engine, expire_on_commit=False)

    # DataBase
    database_name_seed = "genlayer"
    db_uri = f"postgresql+psycopg2://{environ.get('DBUSER')}:{environ.get('DBPASSWORD')}@{environ.get('DBHOST')}/{get_db_name(database_name_seed)}"
    sqlalchemy_db = SQLAlchemy(
        model_class=Base,
        session_options={
            "expire_on_commit": False
        },  # recommended in https://docs.sqlalchemy.org/en/20/orm/session_basics.html#when-do-i-construct-a-session-when-do-i-commit-it-and-when-do-i-close-it
    )

    # Enable SQLAlchemy logging through the logging system (which we intercept)
    import logging

    sqlalchemy_logger = logging.getLogger("sqlalchemy.engine")
    sqlalchemy_logger.setLevel(logging.INFO)

    # Flask
    app = Flask("jsonrpc_api")
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_ECHO"] = (
        False  # We handle SQL logging through Loguru intercept
    )
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": 100,
        "max_overflow": 50,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "pool_timeout": 30,
        "echo_pool": True,  # temporary for verifying pool behavior
    }
    sqlalchemy_db.init_app(app)

    # Use the Flask-SQLAlchemy engine everywhere
    with app.app_context():
        engine = sqlalchemy_db.engine

    CORS(app, resources={r"/api/*": {"origins": "*"}}, intercept_exceptions=False)
    jsonrpc = JSONRPC(
        app, "/api", enable_web_browsable_api=True
    )  # check it out at http://localhost:4000/api/browse/#/
    setup_eth_method_handler(jsonrpc)
    socketio = SocketIO(app, cors_allowed_origins="*")
    # Handlers
    msg_handler = MessageHandler(socketio, config=GlobalConfiguration())
    session_for_type = cast(Session, sqlalchemy_db.session)
    transactions_processor = TransactionsProcessor(session_for_type)
    accounts_manager = AccountsManager(session_for_type)
    snapshot_manager = SnapshotManager(session_for_type)
    validators_registry = ValidatorsRegistry(session_for_type)
    with app.app_context():
        llm_provider_registry = LLMProviderRegistry(session_for_type)
        llm_provider_registry.update_defaults()
    consensus_service = ConsensusService()
    transactions_parser = TransactionParser(consensus_service)

    # Initialize validators with managed session
    with managed_session(create_session) as session:
        validators_config = os.environ.get("VALIDATORS_CONFIG_JSON")
        if validators_config:
            await initialize_validators(
                validators_config,
                ModifiableValidatorsRegistry(session),
                AccountsManager(session),
            )
        else:
            print("VALIDATORS_CONFIG_JSON not set, skipping validator initialization")

    validators_manager = validators.Manager(create_session())
    await validators_manager.restart()

    validators_registry = validators_manager.registry

    consensus = ConsensusAlgorithm(
        create_session,
        msg_handler,
        consensus_service,
        validators_manager,
    )
    return (
        app,
        jsonrpc,
        socketio,
        msg_handler,
        session_for_type,
        accounts_manager,
        snapshot_manager,
        transactions_processor,
        validators_registry,
        consensus,
        llm_provider_registry,
        sqlalchemy_db,
        consensus_service,
        transactions_parser,
        validators_manager,
    )


import asyncio

load_dotenv()

(
    app,
    jsonrpc,
    socketio,
    msg_handler,
    request_session,
    accounts_manager,
    snapshot_manager,
    transactions_processor,
    validators_registry,
    consensus,
    llm_provider_registry,
    sqlalchemy_db,
    consensus_service,
    transactions_parser,
    validators_manager,
) = MAIN_SERVER_LOOP.run_until_complete(create_app())

register_all_rpc_endpoints(
    jsonrpc,
    msg_handler,
    request_session,
    accounts_manager,
    snapshot_manager,
    transactions_processor,
    validators_registry,
    validators_manager,
    llm_provider_registry,
    consensus,
    consensus_service,
    transactions_parser,
    sqlalchemy_db,
)


def restore_stuck_transactions(session_factory):
    """Restore transactions that are stuck because of a program crash or shutdown. If they cannot be restored, they are deleted."""

    def transaction_to_canceled(
        transactions_processor: TransactionsProcessor,
        msg_handler: MessageHandler,
        transaction_hash: str,
    ):
        try:
            ConsensusAlgorithm.dispatch_transaction_status_update(
                transactions_processor,
                transaction_hash,
                TransactionStatus.CANCELED,
                msg_handler,
            )
        except Exception as e:
            print(
                f"ERROR: Failed to put transaction to canceled status {transaction_hash}: {str(e)}"
            )
            transactions_processor.set_transaction_appeal_leader_timeout(
                transaction_hash, False
            )
            transactions_processor.set_leader_timeout_validators(transaction_hash, [])
            transactions_processor.set_transaction_appeal_validators_timeout(
                transaction_hash, False
            )

    def get_previous_contract_state(transaction: dict) -> dict:
        leader_receipt = transaction["consensus_data"]["leader_receipt"]
        if isinstance(leader_receipt, list):
            previous_contract_state = leader_receipt[0]["contract_state"]
        else:
            previous_contract_state = leader_receipt["contract_state"]
        return previous_contract_state

    # Use managed session for the entire restore operation
    with managed_session(session_factory) as session:
        # Create processors with the managed session
        local_transactions_processor = TransactionsProcessor(session)
        local_accounts_manager = AccountsManager(session)

        try:
            # Find oldest stuck transaction per contract
            stuck_transactions = (
                local_transactions_processor.transactions_in_process_by_contract()
            )
        except Exception as e:
            print(
                f"ERROR: Failed to find stuck transactions. Nothing restored: {str(e)}"
            )
            return

        for tx2 in stuck_transactions:
            # Restore the contract state
            try:
                contract_processor = contract_processor_factory(session)

                if tx2["type"] == TransactionType.DEPLOY_CONTRACT.value:
                    contract_reset = contract_processor.reset_contract(
                        contract_address=tx2["to_address"]
                    )

                    if not contract_reset:
                        local_accounts_manager.create_new_account_with_address(
                            tx2["to_address"]
                        )
                else:
                    tx1_finalized = (
                        local_transactions_processor.get_previous_transaction(
                            tx2["hash"], TransactionStatus.FINALIZED, True
                        )
                    )
                    tx1_accepted = (
                        local_transactions_processor.get_previous_transaction(
                            tx2["hash"], TransactionStatus.ACCEPTED, True
                        )
                    )

                    if tx1_finalized:
                        previous_finalized_state = get_previous_contract_state(
                            tx1_finalized
                        )
                        if tx1_accepted:
                            if tx1_accepted["created_at"] > tx1_finalized["created_at"]:
                                previous_accepted_state = get_previous_contract_state(
                                    tx1_accepted
                                )
                            else:
                                previous_accepted_state = previous_finalized_state
                        else:
                            previous_accepted_state = previous_finalized_state
                    else:
                        previous_finalized_state = {}
                        if tx1_accepted:
                            previous_accepted_state = get_previous_contract_state(
                                tx1_accepted
                            )
                        else:
                            previous_accepted_state = {}

                    contract_processor.update_contract_state(
                        contract_address=tx2["to_address"],
                        accepted_state=previous_accepted_state,
                        finalized_state=previous_finalized_state,
                    )

            except Exception as e:
                print(
                    f"ERROR: Failed to restore contract state {tx2['to_address']} for transaction {tx2['hash']}: {str(e)}"
                )
                # managed_session will handle rollback automatically

            else:
                # Restore the transactions
                try:
                    newer_transactions = (
                        local_transactions_processor.get_newer_transactions(tx2["hash"])
                    )
                except Exception as e:
                    print(
                        f"ERROR: Failed to get newer transactions for {tx2['hash']}. Nothing restored: {str(e)}"
                    )
                    transaction_to_canceled(
                        local_transactions_processor, msg_handler, tx2["hash"]
                    )
                else:
                    restore_transactions = [tx2, *newer_transactions]

                    for restore_transaction in restore_transactions:
                        try:
                            if (
                                local_accounts_manager.get_account(
                                    restore_transaction["to_address"]
                                )
                                is None
                            ):
                                transaction_to_canceled(
                                    local_transactions_processor,
                                    msg_handler,
                                    restore_transaction["hash"],
                                )
                            else:
                                ConsensusAlgorithm.dispatch_transaction_status_update(
                                    local_transactions_processor,
                                    restore_transaction["hash"],
                                    TransactionStatus.PENDING,
                                    msg_handler,
                                )
                                local_transactions_processor.set_transaction_contract_snapshot(
                                    restore_transaction["hash"], None
                                )
                                local_transactions_processor.set_transaction_result(
                                    restore_transaction["hash"], None
                                )
                                local_transactions_processor.set_transaction_appeal(
                                    restore_transaction["hash"], False
                                )
                                local_transactions_processor.set_transaction_appeal_failed(
                                    restore_transaction["hash"], 0
                                )
                                local_transactions_processor.set_transaction_appeal_undetermined(
                                    restore_transaction["hash"], False
                                )
                                local_transactions_processor.reset_consensus_history(
                                    restore_transaction["hash"]
                                )
                                local_transactions_processor.set_transaction_timestamp_appeal(
                                    restore_transaction["hash"], None
                                )
                                local_transactions_processor.reset_transaction_appeal_processing_time(
                                    restore_transaction["hash"]
                                )
                        except Exception as e:
                            print(
                                f"ERROR: Failed to reset transaction {restore_transaction['hash']}: {str(e)}"
                            )
                            transaction_to_canceled(
                                local_transactions_processor,
                                msg_handler,
                                restore_transaction["hash"],
                            )


# Restore stuck transactions
with app.app_context():
    # Provide a create_session factory at module scope using the returned sqlalchemy_db
    def create_session():
        return Session(sqlalchemy_db.engine, expire_on_commit=False)

    restore_stuck_transactions(create_session)


# This ensures that the transaction is committed or rolled back depending on the success of the request.
# Opinions on whether this is a good practice are divided https://github.com/pallets-eco/flask-sqlalchemy/issues/216
@app.teardown_appcontext
def shutdown_session(exception=None):
    if exception:
        sqlalchemy_db.session.rollback()  # Rollback if there is an exception
    else:
        sqlalchemy_db.session.commit()  # Commit if everything is fine
    sqlalchemy_db.session.remove()  # Remove the session after every request


async def main():
    def run_socketio():
        socketio.run(
            app,
            debug=os.getenv("VSCODEDEBUG", "false") == "false",
            port=int(os.environ.get("RPCPORT", "4000")),
            host="0.0.0.0",
            allow_unsafe_werkzeug=True,
        )

        @socketio.on("subscribe")
        def handle_subscribe(topics):
            for topic in topics:
                join_room(topic)

        @socketio.on("unsubscribe")
        def handle_unsubscribe(topics):
            for topic in topics:
                leave_room(topic)

    # Thread for the Flask-SocketIO server
    threading.Thread(target=run_socketio, daemon=True).start()

    stop_event = threading.Event()

    async def convert_future_to_event():
        await MAIN_LOOP_EXITING
        stop_event.set()

    futures = [
        consensus.run_crawl_snapshot_loop(stop_event=stop_event),
        consensus.run_process_pending_transactions_loop(stop_event=stop_event),
        consensus.run_appeal_window_loop(stop_event=stop_event),
        convert_future_to_event(),
    ]

    def taskify(f):
        async def inner():
            try:
                return await f
            except BaseException as e:
                import traceback

                traceback.print_exc()
                raise

        return asyncio.tasks.create_task(inner())

    try:
        await asyncio.wait([taskify(f) for f in futures], return_when="ALL_COMPLETED")
    finally:
        print("starting validators manager termination")
        await validators_manager.terminate()
        print("awaited termination")


def app_target():
    try:
        MAIN_SERVER_LOOP.run_until_complete(main())
    except BaseException as e:
        MAIN_LOOP_DONE.set_exception(e)
    finally:
        MAIN_LOOP_DONE.set_result(True)


threading.Thread(target=app_target, daemon=True).start()


def atexit_handler():
    print("initiating shutdown")

    def shutdown():
        MAIN_LOOP_EXITING.set_result(True)

    MAIN_SERVER_LOOP.call_soon_threadsafe(shutdown)
    print("awaiting threads")
    MAIN_LOOP_DONE.result()
    print("shutdown done")


import atexit

atexit.register(atexit_handler)
