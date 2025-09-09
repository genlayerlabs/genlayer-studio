You're leaking DB connections from the consensus loops.

TL;DR (root cause)

In both pending-tx processing and appeal windows you:

open a Session in a with self.get_session() as session: block,

define an async task that uses that session,

schedule the task after the with block exits.

When the coroutine later touches the session, SQLAlchemy re-opens a new DB connection for it, and because you never close that session inside the task, that connection stays checked out. Under load, these accumulate until the pool hits pool_size + max_overflow and every RPC starts timing out. Only restarting the jsonrpc container resets the pool.

This exact pattern appears twice:

ConsensusAlgorithm._process_pending_transactions

ConsensusAlgorithm._appeal_window

Minimal fix (make each coroutine own & close its session)
backend/consensus/base.py — _process_pending_transactions

Before (problematic):

with self.get_session() as session:

    async def exec_transaction_with_session_handling(
        session: Session,
        transaction: Transaction,
        queue_address: str,
    ):
        transactions_processor = transactions_processor_factory(session)
        async with (self.validators_manager.snapshot() as validators_snapshot):
            await self.exec_transaction(...)
        session.commit()
        self.pending_queue_task_running[queue_address] = False

tg.create_task(
    exec_transaction_with_session_handling(session, transaction, queue_address)
)


After (safe):

async def exec_transaction_with_session_handling(
    tx: Transaction,
    queue_address: str,
):
    try:
        with self.get_session() as session:
            transactions_processor = transactions_processor_factory(session)
            async with (self.validators_manager.snapshot() as validators_snapshot):
                await self.exec_transaction(
                    tx,
                    transactions_processor,
                    chain_snapshot_factory(session),
                    accounts_manager_factory(session),
                    lambda addr: contract_snapshot_factory(addr, session, tx),
                    contract_processor_factory(session),
                    node_factory,
                    validators_snapshot,
                )
            session.commit()
    finally:
        self.pending_queue_task_running[queue_address] = False

tg.create_task(exec_transaction_with_session_handling(transaction, queue_address))

backend/consensus/base.py — _appeal_window

Before (problematic):

with self.get_session() as task_session:

    async def exec_appeal_window_with_session_handling(
        task_session: Session,
        awaiting_finalization_queue: list[dict],
        captured_chain_snapshot: ChainSnapshot = chain_snapshot,
    ):
        transactions_processor = transactions_processor_factory(task_session)
        # ... uses task_session many times ...
        task_session.commit()

    tg.create_task(
        exec_appeal_window_with_session_handling(task_session, awaiting_finalization_queue)
    )


After (safe):

async def exec_appeal_window_with_session_handling(
    awaiting_finalization_queue: list[dict],
    captured_chain_snapshot: ChainSnapshot = chain_snapshot,
):
    with self.get_session() as task_session:
        transactions_processor = transactions_processor_factory(task_session)
        # ... use task_session for the whole loop ...
        # commit where you already do it (per operation) or once at end if suitable

tg.create_task(
    exec_appeal_window_with_session_handling(awaiting_finalization_queue)
)


Why this fixes it: each coroutine now creates its own session, checks out a connection, commits, and returns the connection when the with block exits—no orphaned checkouts.

How to verify quickly

Hit your existing debug endpoint repeatedly while load is running:

// method: "dev_getPoolStatus"


You should see checked_out no longer climb unbounded; it should return to a small, stable number.

Watch logs (you already have echo_pool=True). Before the fix you’ll see increasing “checked out” with no corresponding “checkin”; after the fix, each coroutine’s session will issue a checkin when the with ends.

Optional hardening (nice to have)

In those two coroutines, wrap the body in try/except and still ensure the session scope exits (the with already guarantees close).

If you want stricter semantics, use explicit transactional scopes:

with self.get_session() as session:
    with session.begin():
        ...


Consider removing the unconditional commit in @app.teardown_appcontext and just remove() the scoped session; let endpoints decide when to commit:

@app.teardown_appcontext
def shutdown_session(exception=None):
    if exception:
        sqlalchemy_db.session.rollback()
    sqlalchemy_db.session.remove()


(Not required for the leak, but safer for long-lived request contexts.)

Notes on other suspects you can ignore

ChainSnapshot deletes its session reference immediately; it doesn’t hold a connection.

The Web3ConnectionPool has a tiny HTTP pool (pool_maxsize=1), but that would show as HTTP timeouts, not DB pool exhaustion logs.

The restore-stuck-tx path uses managed_session and closes properly.

Apply the two code changes above and the pool exhaustion from RPC methods should disappear.