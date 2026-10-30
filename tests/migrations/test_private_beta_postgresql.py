"""Opt-in acceptance check against an explicitly supplied EMPTY local PostgreSQL DB."""

import importlib.util
import os
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
POSTGRES_URL = os.environ.get("BETA_POSTGRES_TEST_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="Set BETA_POSTGRES_TEST_URL to an empty, disposable loopback test database",
)

# Reuse the exact mocked-AI acceptance journey, with the database fixture below.
_spec = importlib.util.spec_from_file_location(
    "beta_postgres_journey", ROOT / "tests/routes/test_private_beta.py"
)
assert _spec and _spec.loader
_journey = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_journey)
beta = _journey.beta


@pytest.fixture
def isolated_session_factory(monkeypatch):
    from app import database, main
    from app.database import Base

    target = make_url(POSTGRES_URL)
    assert target.get_backend_name() == "postgresql"
    assert target.host == "127.0.0.1" and target.port and target.port != 5432
    assert target.database and target.database.startswith("radar_beta_test_")
    engine = create_engine(target)
    assert not inspect(engine).get_table_names(), (
        "This check requires an EMPTY disposable database"
    )
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            compare_metadata(MigrationContext.configure(connection), Base.metadata)
            == []
        )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(main, "SessionLocal", factory)
    yield factory
    engine.dispose()


def test_fresh_postgresql_migrations_and_complete_two_workspace_journey(beta):
    _journey.test_private_beta_complete_journey_and_two_workspace_isolation(beta)
