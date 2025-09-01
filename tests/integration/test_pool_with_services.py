import pytest
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.models import Base
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from concurrent.futures import ThreadPoolExecutor
import os


class TestPoolIntegrationWithServices:
    """Integration tests for connection pool with existing services"""
    
    @pytest.fixture
    def pool_optimized_engine(self):
        """Engine with optimized pool configuration for services"""
        postgres_url = os.getenv("POSTGRES_URL",
                                 "postgresql+psycopg2://postgres:postgres@postgrestest:5432/postgres")
        engine = create_engine(
            postgres_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30
        )
        Base.metadata.create_all(engine)
        yield engine
        Base.metadata.drop_all(engine)
        engine.dispose()
    
    def test_transactions_processor_with_pool(self, pool_optimized_engine):
        """Verify TransactionsProcessor with optimized pool configuration"""
        session_maker = sessionmaker(bind=pool_optimized_engine)
        
        def process_transaction():
            session = session_maker()
            try:
                processor = TransactionsProcessor(session)
                # Basic operation to verify pool works
                session.execute(text("SELECT 1"))
                session.commit()
                return True
            except Exception as e:
                session.rollback()
                return False
            finally:
                session.close()
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(process_transaction) for _ in range(30)]
            results = [f.result() for f in futures]
        
        assert all(results)
    
    def test_accounts_manager_concurrent_access(self, pool_optimized_engine):
        """Verify AccountsManager with concurrent access"""
        session_maker = sessionmaker(bind=pool_optimized_engine)
        
        def manage_account(account_id):
            session = session_maker()
            try:
                manager = AccountsManager(session)
                # Basic operation to verify pool works
                session.execute(text("SELECT 1"))
                session.commit()
                return True
            except Exception:
                session.rollback()
                return False
            finally:
                session.close()
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(manage_account, i) for i in range(20)]
            results = [f.result() for f in futures]
        
        assert all(results)
    
    def test_session_manager_with_pool(self, pool_optimized_engine):
        """Test SessionManager with pool configuration"""
        from backend.database_handler.session_manager import SessionManager
        
        session_maker = sessionmaker(bind=pool_optimized_engine)
        
        def work_with_session_manager(worker_id):
            try:
                with SessionManager(session_maker) as session:
                    result = session.execute(text("SELECT :id as worker_id"), {"id": worker_id})
                    assert result.scalar() == worker_id
                return True
            except Exception:
                return False
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(work_with_session_manager, i) for i in range(30)]
            results = [f.result() for f in futures]
        
        assert all(results)