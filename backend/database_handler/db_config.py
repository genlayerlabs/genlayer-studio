# backend/database_handler/db_config.py
"""
Database configuration utilities for scalability
"""
import os

def get_db_url() -> str:
    """Get database URL from environment variables"""
    dbhost = os.getenv('DBHOST', 'postgres')
    dbport = os.getenv('DBPORT', '5432')
    dbuser = os.getenv('DBUSER', 'genlayer')
    dbpassword = os.getenv('DBPASSWORD', 'genlayer')
    dbname = os.getenv('DBNAME', 'genlayer')
    
    return f"postgresql://{dbuser}:{dbpassword}@{dbhost}:{dbport}/{dbname}"

def get_db_config() -> dict:
    """Get database configuration as dictionary"""
    return {
        'host': os.getenv('DBHOST', 'postgres'),
        'port': int(os.getenv('DBPORT', '5432')),
        'user': os.getenv('DBUSER', 'genlayer'),
        'password': os.getenv('DBPASSWORD', 'genlayer'),
        'database': os.getenv('DBNAME', 'genlayer')
    }

def get_async_db_url() -> str:
    """Get async database URL for asyncpg"""
    config = get_db_config()
    return f"postgresql+asyncpg://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}"