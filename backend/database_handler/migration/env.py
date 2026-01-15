from logging.config import fileConfig
import sys

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
import os

# set up Python path as the project root directory, so that we can import as backend...
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

# Construct database URL from individual environment variables for consistency with other services
db_user = os.environ.get("DBUSER", "postgres")
db_password = os.environ.get("DBPASSWORD", "postgres")
db_host = os.environ.get("DBHOST", "localhost")
db_port = os.environ.get("DBPORT", "5432")
db_name = os.environ.get("DBNAME", "genlayer")

DB_URL = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
url = DB_URL if DB_URL is not None else config.get_main_option("sqlalchemy.url")

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
from backend.database_handler.models import Base

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    print("Running migrations offline")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    print("Running migrations online")

    config_section = config.get_section(config.config_ini_section, {})
    config_section["sqlalchemy.url"] = url

    connectable = engine_from_config(
        config_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
