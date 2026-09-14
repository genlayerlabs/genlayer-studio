import asyncio
import json
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.pool import QueuePool

from backend.database_handler.session_factory import DatabaseSessionManager
from backend.protocol_rpc.explorer import queries, query_runner
from backend.protocol_rpc.explorer.query_runner import ExplorerQueryRunner
from backend.protocol_rpc.explorer.router import explorer_router


async def asgi_get(app, url):
    """Exercise the app without an optional HTTP test-client dependency."""
    path, _, query = url.partition("?")
    messages = []
    request_sent = False
    response_done = asyncio.Event()

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await response_done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            response_done.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [],
        "server": ("test", 80),
        "client": ("test", 1234),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    return start["status"], headers, json.loads(body)


class DatabaseStub:
    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.session = object()

    @contextmanager
    def open_session(self):
        self.opened += 1
        try:
            yield self.session
        finally:
            self.closed += 1


@pytest.mark.parametrize("fails", [False, True])
def test_real_session_returns_connection_and_rolls_back(fails):
    db = DatabaseSessionManager("sqlite://", poolclass=QueuePool)
    runner = ExplorerQueryRunner(db)
    try:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE example (value INTEGER)"))

        def query(session):
            session.execute(text("INSERT INTO example VALUES (1)"))
            if fails:
                raise ValueError("query failed")
            return session.execute(text("SELECT count(*) FROM example")).scalar()

        if fails:
            with pytest.raises(ValueError, match="query failed"):
                runner.run(query)
        else:
            assert runner.run(query) == 1

        assert db.engine.pool.checkedout() == 0
        # Explorer does not commit writes; ending the read session rolls back.
        assert (
            runner.run(
                lambda session: session.execute(
                    text("SELECT count(*) FROM example")
                ).scalar()
            )
            == 0
        )
        assert db.engine.pool.checkedout() == 0
    finally:
        db.engine.dispose()


@pytest.mark.parametrize("fails", [False, True])
def test_session_closes_before_return_and_admission_is_reusable(fails):
    db = DatabaseStub()
    runner = ExplorerQueryRunner(db, max_concurrent=1)

    def query(session, value, *, option):
        assert session is db.session
        assert db.closed == 0
        if fails:
            raise ValueError("query failed")
        return {"value": value, "option": option}

    if fails:
        with pytest.raises(ValueError, match="query failed"):
            runner.run(query, 1, option=2)
    else:
        assert runner.run(query, 1, option=2) == {"value": 1, "option": 2}

    assert db.opened == db.closed == 1
    assert runner.run(lambda session: "next") == "next"
    assert db.opened == db.closed == 2


def test_admission_rejects_before_opening_session():
    db = DatabaseStub()
    runner = ExplorerQueryRunner(db, max_concurrent=2)

    def second_query(session):
        with pytest.raises(HTTPException) as exc:
            runner.run(lambda session: pytest.fail("must not run"))
        assert exc.value.status_code == 503
        assert exc.value.headers == {"Retry-After": "1"}
        assert db.opened == 2

    runner.run(lambda session: runner.run(second_query))
    assert db.opened == db.closed == 2
    assert runner.run(lambda session: "next") == "next"


def test_admission_released_when_open_session_fails():
    db = Mock()
    db.open_session.side_effect = RuntimeError("unavailable")
    runner = ExplorerQueryRunner(db, max_concurrent=1)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="unavailable"):
            runner.run(lambda session: None)
    assert db.open_session.call_count == 2


def test_counts_cache_expires_and_does_not_share_mutable_results(monkeypatch):
    db = DatabaseStub()
    runner = ExplorerQueryRunner(db, counts_ttl=5)
    now = [100.0]
    monkeypatch.setattr(query_runner.time, "monotonic", lambda: now[0])
    count_query = Mock(side_effect=[{"transactions": 10}, {"transactions": 11}])
    monkeypatch.setattr(queries, "get_stats_counts", count_query)

    first = runner.counts()
    first["transactions"] = -1
    assert runner.counts() == {"transactions": 10}
    cached = runner.counts()
    cached["transactions"] = -2
    assert runner.counts() == {"transactions": 10}
    assert db.opened == db.closed == 1

    now[0] = 105.0
    assert runner.counts() == {"transactions": 11}
    assert db.opened == db.closed == 2


@pytest.mark.parametrize("has_cached_counts", [False, True])
def test_counts_refresh_is_coalesced(monkeypatch, has_cached_counts):
    db = DatabaseStub()
    runner = ExplorerQueryRunner(db, counts_ttl=0)
    if has_cached_counts:
        monkeypatch.setattr(queries, "get_stats_counts", lambda session: {"count": 1})
        assert runner.counts() == {"count": 1}

    def refresh(session):
        opened = db.opened
        if has_cached_counts:
            assert runner.counts() == {"count": 1}
        else:
            with pytest.raises(HTTPException) as exc:
                runner.counts()
            assert exc.value.status_code == 503
        assert db.opened == opened
        return {"count": 2}

    monkeypatch.setattr(queries, "get_stats_counts", refresh)
    assert runner.counts() == {"count": 2}
    assert db.opened == db.closed == (2 if has_cached_counts else 1)


def test_failed_counts_refresh_can_retry(monkeypatch):
    db = DatabaseStub()
    runner = ExplorerQueryRunner(db)
    monkeypatch.setattr(
        queries,
        "get_stats_counts",
        Mock(side_effect=[ValueError("failed"), {"count": 2}]),
    )
    with pytest.raises(ValueError, match="failed"):
        runner.counts()
    assert runner.counts() == {"count": 2}
    assert db.opened == db.closed == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, query_name, args, kwargs",
    [
        ("/stats", "get_stats", (), {}),
        ("/stats/counts", "get_stats_counts", (), {}),
        (
            "/transactions?page=2&limit=10&status=PENDING&search=x&from_date=a&to_date=b&address=c",
            "get_all_transactions_paginated",
            (2, 10, "PENDING", "x", "a", "b", "c"),
            {},
        ),
        ("/transactions/0xabc", "get_transaction_with_relations", ("0xabc",), {}),
        (
            "/validators?search=x&limit=4",
            "get_all_validators",
            (),
            {"search": "x", "limit": 4},
        ),
        ("/address/0xabc", "get_address_info", ("0xabc",), {}),
        (
            "/contracts?search=x&page=2&limit=4&sort_by=tx_count&sort_order=asc",
            "get_all_states",
            ("x", 2, 4, "tx_count", "asc"),
            {},
        ),
        ("/providers", "get_all_providers", (), {}),
    ],
)
async def test_routes_use_bounded_worker_sessions(
    monkeypatch, path, query_name, args, kwargs
):
    import threading

    db = DatabaseStub()
    app = FastAPI()
    app.state.explorer_query_runner = ExplorerQueryRunner(db)
    app.include_router(explorer_router)
    loop_threads = []
    query_threads = []

    @app.middleware("http")
    async def note_loop(request, call_next):
        loop_threads.append(threading.get_ident())
        response = await call_next(request)
        assert db.closed == 1
        return response

    def query(session, *query_args, **query_kwargs):
        query_threads.append(threading.get_ident())
        assert session is db.session
        assert query_args == args
        assert query_kwargs == kwargs
        return {"result": "ok"}

    monkeypatch.setattr(queries, query_name, query)
    status, _, body = await asgi_get(app, "/api/explorer" + path)
    assert status == 200
    assert body == {"result": "ok"}
    assert query_threads[0] != loop_threads[0]


@pytest.mark.asyncio
async def test_routes_preserve_not_found_validation_and_busy_responses(monkeypatch):
    db = DatabaseStub()
    app = FastAPI()
    runner = ExplorerQueryRunner(db, max_concurrent=1)
    app.state.explorer_query_runner = runner
    app.include_router(explorer_router)
    monkeypatch.setattr(queries, "get_transaction_with_relations", lambda *args: None)
    monkeypatch.setattr(queries, "get_address_info", lambda *args: None)
    for path in ("/transactions/missing", "/address/missing"):
        assert (await asgi_get(app, "/api/explorer" + path))[0] == 404
    assert db.opened == db.closed == 2
    for path in ("/transactions?limit=101", "/contracts?sort_by=invalid"):
        assert (await asgi_get(app, "/api/explorer" + path))[0] == 422
    assert db.opened == 2
    with runner._slots:
        status, headers, _ = await asgi_get(app, "/api/explorer/stats")
        assert status == 503
        assert headers["retry-after"] == "1"
    assert db.opened == 2


@pytest.mark.asyncio
async def test_uninitialized_explorer_returns_503():
    app = FastAPI()
    app.include_router(explorer_router)
    assert (await asgi_get(app, "/api/explorer/stats"))[0] == 503
