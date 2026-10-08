from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db import create_db_engine, create_session_factory, run_migrations
from app.main import create_app
from app.storage import LocalStorage
from tests.fixtures.generate import generate_all


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("fixtures")
    generate_all(out)
    return out


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'db' / 'test.db').as_posix()}",
        storage_dir=tmp_path / "uploads",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    engine = create_db_engine(settings.database_url)
    run_migrations(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def storage(settings: Settings) -> LocalStorage:
    storage = LocalStorage(settings.storage_dir, settings.max_upload_bytes)
    storage.prepare()
    return storage
