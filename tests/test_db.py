from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text

from app.db import create_db_engine
from app.models import Base


def test_sqlite_parent_directory_is_created(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "dir" / "app.db"

    engine = create_db_engine(f"sqlite:///{database.as_posix()}")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    engine.dispose()

    assert database.exists()


def test_sqlite_uses_wal_and_foreign_keys(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_migrations_match_the_models(engine: Engine) -> None:
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert diff == []
