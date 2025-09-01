import os
from typing import Iterable

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.database_handler.models import Base
from backend.database_handler.transactions_processor import TransactionsProcessor

import debugpy


debugpy.listen(("0.0.0.0", 5678))
if os.getenv("WAIT_FOR_DEBUGGER"):
    # TODO: this is not printing anything
    print("Waiting for debugger to attach...")
    debugpy.wait_for_client()
    print("Debugger attached")


@pytest.fixture
def engine() -> Iterable[Engine]:
    postgres_url = os.getenv("POSTGRES_URL")
    engine = create_engine(
        postgres_url,
        # echo=True # Uncomment this line to see the SQL queries
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def session(engine: Engine) -> Iterable[Session]:
    session_maker = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_maker()
    yield session
    session.close()


@pytest.fixture
def transactions_processor(session: Session) -> Iterable[TransactionsProcessor]:
    yield TransactionsProcessor(session)


@pytest.fixture
def configurable_engine():
    """Engine with configurable pool settings for testing"""
    def _create_engine(**pool_kwargs):
        postgres_url = os.getenv("POSTGRES_URL",
                                 "postgresql+psycopg2://postgres:postgres@postgrestest:5432/postgres")
        default_config = {
            'pool_size': 5,
            'max_overflow': 10,
            'pool_pre_ping': True,
            'pool_recycle': 3600,
            'pool_timeout': 30
        }
        default_config.update(pool_kwargs)
        engine = create_engine(postgres_url, **default_config)
        Base.metadata.create_all(engine)
        yield engine
        Base.metadata.drop_all(engine)
        engine.dispose()
    
    return _create_engine


@pytest.fixture
def pool_monitored_engine():
    """Engine with pool event monitoring for testing"""
    postgres_url = os.getenv("POSTGRES_URL")
    
    pool_events = {
        'connects': 0,
        'checkouts': 0,
        'checkins': 0,
        'connect_errors': 0
    }
    
    engine = create_engine(
        postgres_url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True
    )
    
    from sqlalchemy import event
    
    @event.listens_for(engine, "connect")
    def receive_connect(dbapi_conn, connection_record):
        pool_events['connects'] += 1
    
    @event.listens_for(engine, "checkout")
    def receive_checkout(dbapi_conn, connection_record, connection_proxy):
        pool_events['checkouts'] += 1
    
    @event.listens_for(engine, "checkin")
    def receive_checkin(dbapi_conn, connection_record):
        pool_events['checkins'] += 1
    
    engine.pool_events = pool_events
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()
