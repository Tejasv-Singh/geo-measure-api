"""Shared fixtures.

Tests use a fresh SQLite file per test. When DATABASE_URL points at Postgres, as in CI, they use
a fresh Postgres database per test instead: migrations run once into a template database, and
each test gets a copy made with CREATE DATABASE ... TEMPLATE, dropped afterwards.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db import create_db_engine, create_session_factory, run_migrations
from app.main import create_app
from app.storage import LocalStorage
from tests.fixtures.generate import generate_all


def postgres_url() -> URL | None:
    value = os.environ.get("DATABASE_URL")
    if not value:
        return None
    url = make_url(value)
    return url if url.get_backend_name() == "postgresql" else None


def render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("fixtures")
    generate_all(out)
    return out


@pytest.fixture(scope="session")
def postgres_admin() -> Iterator[Engine | None]:
    url = postgres_url()
    if url is None:
        yield None
        return
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    yield admin
    admin.dispose()


@pytest.fixture(scope="session")
def postgres_template(postgres_admin: Engine | None) -> Iterator[str | None]:
    if postgres_admin is None:
        yield None
        return
    name = f"geo_test_template_{uuid.uuid4().hex[:8]}"
    with postgres_admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_db_engine(render(postgres_admin.url.set(database=name)))
    run_migrations(engine)
    # A template must have no open connections when it is copied.
    engine.dispose()
    yield name
    with postgres_admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def database_url(
    tmp_path: Path, postgres_admin: Engine | None, postgres_template: str | None
) -> Iterator[str]:
    if postgres_admin is None or postgres_template is None:
        yield f"sqlite:///{(tmp_path / 'db' / 'test.db').as_posix()}"
        return
    name = f"geo_test_{uuid.uuid4().hex[:12]}"
    with postgres_admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{postgres_template}"'))
    yield render(postgres_admin.url.set(database=name))
    with postgres_admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def settings(tmp_path: Path, database_url: str) -> Settings:
    return Settings(database_url=database_url, storage_dir=tmp_path / "uploads")


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
