from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from app.core.config import get_settings
from app.db import create_db_engine
from app.models import Base

config = context.config
target_metadata = Base.metadata


def configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    # The app passes its own connection from the lifespan handler.
    connection = config.attributes.get("connection")
    if connection is not None:
        configure(connection)
        return

    if config.config_file_name is not None:
        fileConfig(config.config_file_name)
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.begin() as connection:
            configure(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
