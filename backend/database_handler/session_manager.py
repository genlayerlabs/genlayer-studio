from contextlib import contextmanager
import logging
from typing import Generator, Callable
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


@contextmanager
def get_db_session(session_factory: Callable[[], Session]) -> Generator[Session, None, None]:
    """Context manager for safe session handling"""
    session = session_factory()
    try:
        yield session
        session.commit()
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        session.rollback()
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        session.rollback()
        raise
    finally:
        session.close()


class SessionManager:
    """Session manager for database transactions with automatic commit/rollback"""
    
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory
        self.session = None
    
    def __enter__(self) -> Session:
        self.session = self.session_factory()
        return self.session
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.session is None:
            return
            
        if exc_type:
            self.session.rollback()
        else:
            try:
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise
        finally:
            self.session.close()
            self.session = None