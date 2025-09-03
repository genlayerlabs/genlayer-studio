# backend/database_handler/session_manager.py

from contextlib import contextmanager


@contextmanager
def managed_session(open_session):
    """
    Context manager for database sessions with automatic cleanup.
    
    Ensures that database sessions are properly committed on success,
    rolled back on error, and always closed when done.
    
    Args:
        open_session: A callable that returns a new database session
        
    Yields:
        session: The database session for use within the context
        
    Example:
        with managed_session(create_session) as session:
            # Use session for database operations
            user = session.query(User).first()
    """
    session = open_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()