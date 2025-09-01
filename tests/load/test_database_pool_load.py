import pytest
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.database_handler.models import Base
import os


class TestDatabasePoolLoad:
    """Load tests for database connection pool"""
    
    @pytest.fixture
    def load_test_engine(self):
        """Engine configured for load testing"""
        postgres_url = os.getenv("POSTGRES_URL",
                                 "postgresql+psycopg2://postgres:postgres@postgrestest:5432/postgres")
        engine = create_engine(
            postgres_url,
            pool_size=20,
            max_overflow=30,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_timeout=30
        )
        Base.metadata.create_all(engine)
        yield engine
        Base.metadata.drop_all(engine)
        engine.dispose()
    
    def test_sustained_load(self, load_test_engine):
        """Test sustained load on the pool"""
        session_maker = sessionmaker(bind=load_test_engine)
        
        execution_times = []
        errors = []
        
        def execute_query(query_id):
            start = time.time()
            session = None
            try:
                session = session_maker()
                result = session.execute(
                    text("SELECT pg_sleep(0.01), :id as query_id"),
                    {"id": query_id}
                )
                result.fetchone()
                session.commit()
                execution_times.append(time.time() - start)
                return True
            except Exception as e:
                errors.append(str(e))
                if session:
                    session.rollback()
                return False
            finally:
                if session:
                    session.close()
        
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(execute_query, i) for i in range(500)]
            for future in as_completed(futures):
                future.result()
        
        assert len(errors) == 0, f"Errors found: {errors}"
        assert len(execution_times) == 500
        
        avg_time = statistics.mean(execution_times)
        median_time = statistics.median(execution_times)
        p95_time = statistics.quantiles(execution_times, n=20)[18]
        
        print(f"Average time: {avg_time:.3f}s")
        print(f"Median time: {median_time:.3f}s")
        print(f"95th percentile: {p95_time:.3f}s")
        
        assert avg_time < 1.0, "Average time too high"
        assert p95_time < 2.0, "95th percentile too high"
    
    def test_spike_load(self, load_test_engine):
        """Test spike load on the pool"""
        session_maker = sessionmaker(bind=load_test_engine)
        
        def spike_worker(worker_id):
            session = None
            try:
                session = session_maker()
                session.execute(text("SELECT 1"))
                session.commit()
                return True
            except Exception:
                if session:
                    session.rollback()
                return False
            finally:
                if session:
                    session.close()
        
        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(spike_worker, i) for i in range(100)]
            results = [f.result() for f in futures]
        
        successful = sum(1 for r in results if r)
        assert successful >= 50, f"Only {successful} of 100 were successful"
    
    def test_pool_recovery(self, load_test_engine):
        """Test pool recovery after exhaustion"""
        session_maker = sessionmaker(bind=load_test_engine)
        
        held_sessions = []
        try:
            for i in range(50):
                session = session_maker()
                session.execute(text("SELECT 1"))
                held_sessions.append(session)
        except Exception:
            pass
        
        for session in held_sessions:
            session.close()
        
        time.sleep(1)
        
        recovery_test_passed = False
        session = None
        try:
            session = session_maker()
            session.execute(text("SELECT 1"))
            session.commit()
            recovery_test_passed = True
        finally:
            if session:
                session.close()
        
        assert recovery_test_passed, "Pool did not recover after exhaustion"