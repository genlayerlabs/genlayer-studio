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
