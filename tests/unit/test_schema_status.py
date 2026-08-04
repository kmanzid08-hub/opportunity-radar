from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest

from app.schema_status import SchemaState, inspect_schema


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANAGED_TABLES = (
    "leads",
    "opportunities",
    "proposals",
    "sources",
)


def _database_url(path: Path) -> str:
    assert path.name != "opportunities.db"
    return f"sqlite+pysqlite:///{path.resolve().as_posix()}"


def _execute_sql(path: Path, statements: tuple[str, ...]) -> None:
    connection = sqlite3.connect(path)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def temporary_database() -> Iterator[Path]:
    path = (
        Path(__file__).resolve().parent
        / f".schema-status-{uuid4().hex}.db"
    )
    assert path.name != "opportunities.db"

    try:
        yield path
    finally:
        if path.exists():
            path.unlink()


def test_empty_sqlite_file_is_reported_without_modification(
    temporary_database: Path,
) -> None:
    path = temporary_database
    path.touch()
    before = path.read_bytes()

    status = inspect_schema(_database_url(path))

    assert status.state is SchemaState.EMPTY_DATABASE
    assert status.dialect == "sqlite"
    assert status.tables == ()
    assert status.revision is None
    assert path.read_bytes() == before


def test_fresh_migration_database_is_current(
    temporary_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = temporary_database
    database_url = _database_url(path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ENABLE_INTERNAL_SCHEDULER", "false")
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.upgrade(configuration, "head")
    status = inspect_schema(database_url)

    assert status.state is SchemaState.CURRENT
    assert status.revision == "20260731_01"
    assert status.expected_revision == "20260731_01"
    assert set(MANAGED_TABLES).issubset(status.tables)


def test_complete_managed_schema_without_version_is_reported(
    temporary_database: Path,
) -> None:
    path = temporary_database
    _execute_sql(
        path,
        tuple(f"CREATE TABLE {name} (id INTEGER)" for name in MANAGED_TABLES),
    )

    status = inspect_schema(_database_url(path))

    assert status.state is SchemaState.NO_ALEMBIC_VERSION
    assert status.revision is None
    assert status.tables == tuple(sorted(MANAGED_TABLES))


def test_partial_managed_schema_suspects_drift(
    temporary_database: Path,
) -> None:
    path = temporary_database
    _execute_sql(path, ("CREATE TABLE opportunities (id INTEGER)",))

    status = inspect_schema(_database_url(path))

    assert status.state is SchemaState.SCHEMA_DRIFT_SUSPECTED
    assert status.tables == ("opportunities",)


def test_fake_revision_is_unknown(
    temporary_database: Path,
) -> None:
    path = temporary_database
    _execute_sql(
        path,
        (
            "CREATE TABLE alembic_version (version_num VARCHAR(32))",
            "INSERT INTO alembic_version VALUES ('fake_revision')",
        ),
    )

    status = inspect_schema(_database_url(path))

    assert status.state is SchemaState.UNKNOWN_REVISION
    assert status.revision == "fake_revision"


def test_unsupported_dialect_does_not_connect() -> None:
    status = inspect_schema("mysql://user:password@example.test/radar")

    assert status.state is SchemaState.UNSUPPORTED
    assert status.dialect == "mysql"
    assert status.tables == ()
    assert "password" not in status.database_url


@pytest.mark.parametrize("database_url", ["", " ", "\t\n"])
def test_blank_url_is_rejected(database_url: str) -> None:
    with pytest.raises(ValueError, match="explicit non-empty URL"):
        inspect_schema(database_url)


def test_inspection_does_not_import_runtime_application() -> None:
    assert "app.main" not in sys.modules
    assert "app.internal_scheduler" not in sys.modules
    assert "app.scanner" not in sys.modules
