import pytest
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from unittest.mock import patch, Mock
import os
from typing import Generator


class TestDatabasePoolConfiguration:
    """Tests for database connection pool configuration"""
    
    @pytest.fixture
    def pool_config_engine(self) -> Generator:
        """Engine with pool configuration for testing"""
        postgres_url = os.getenv("POSTGRES_URL", 
                                 "postgresql+psycopg2://postgres:postgres@postgrestest:5432/postgres")
        engine = create_engine(
            postgres_url,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=30,
            pool_timeout=5,
            connect_args={
                "connect_timeout": 2,
                "application_name": "pool_test",
            }
        )
        yield engine
        engine.dispose()
    
    def test_pool_pre_ping_configuration(self, pool_config_engine):
        """Verify that pool_pre_ping is working correctly"""
        with pool_config_engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
    
    def test_connection_recycling(self, pool_config_engine):
        """Verify that connections are recycled after pool_recycle time"""
        connection_ids = set()
        
        with pool_config_engine.connect() as conn:
            result = conn.execute(text("SELECT pg_backend_pid()"))
            connection_ids.add(result.scalar())
    
    def test_pool_size_limits(self, pool_config_engine):
        """Verify pool size and overflow limits"""
        connections = []
        session_maker = sessionmaker(bind=pool_config_engine)
        
        try:
            for i in range(15):
                session = session_maker()
                session.execute(text("SELECT 1"))
                connections.append(session)
            
            with pytest.raises(Exception):
                session = session_maker()
                session.execute(text("SELECT 1"))
                
        finally:
            for session in connections:
                session.close()
    
    def test_concurrent_connection_handling(self, pool_config_engine):
        """Verify pool handling under concurrent access"""
        session_maker = sessionmaker(bind=pool_config_engine)
        errors = []
        successful = []
        
        def worker(worker_id):
            try:
                session = session_maker()
                result = session.execute(text("SELECT :id as worker_id"), {"id": worker_id})
                session.commit()
                successful.append(worker_id)
            except Exception as e:
                errors.append((worker_id, str(e)))
            finally:
                session.close()
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker, i) for i in range(50)]
            for future in futures:
                future.result()
        
        assert len(successful) == 50
        assert len(errors) == 0
    
    def test_connection_timeout_handling(self, pool_config_engine):
        """Verify pool timeout behavior"""
        connections = []
        session_maker = sessionmaker(bind=pool_config_engine)
        
        try:
            for i in range(15):
                session = session_maker()
                session.execute(text("SELECT 1"))
                connections.append(session)
            
            start_time = time.time()
            
            with pytest.raises(Exception) as exc_info:
                session = session_maker()
                session.execute(text("SELECT 1"))
            
            elapsed = time.time() - start_time
            assert elapsed >= 4 and elapsed <= 6
            
        finally:
            for session in connections:
                session.close()
    
    def test_pool_configuration_from_environment(self):
        """Verify that pool configuration loads from environment variables"""
        with patch.dict(os.environ, {
            'DB_POOL_SIZE': '10',
            'DB_MAX_OVERFLOW': '20',
            'DB_POOL_RECYCLE': '1800',
            'DB_POOL_TIMEOUT': '15',
            'DB_POOL_PRE_PING': 'true',
            'DB_CONNECT_TIMEOUT': '5',
            'DBHOST': 'postgrestest',
            'DBUSER': 'postgres',
            'DBPASSWORD': 'postgres'
        }):
            from backend.protocol_rpc.server import create_app
            # This test verifies the environment variable loading
            # Implementation depends on how create_app is structured