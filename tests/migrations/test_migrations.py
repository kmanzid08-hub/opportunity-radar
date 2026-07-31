from __future__ import annotations

import builtins
from collections.abc import Iterator
from pathlib import Path
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

import app.lead_models  # noqa: F401
import app.models  # noqa: F401
import app.proposal_models  # noqa: F401
from app.database import Base


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANAGED_TABLES = {
    "leads",
    "opportunities",
    "proposals",
    "sources",
}


def _database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.resolve().as_posix()}"


def _alembic_config() -> Config:
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))
    configuration.set_main_option(
        "script_location",
        str(PROJECT_ROOT / "migrations"),
    )
    return configuration


@pytest.fixture
def migration_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, str]]:
    path = (
        Path(__file__).resolve().parent
        / f".migration-{uuid4().hex}.db"
    )
    url = _database_url(path)
    assert path.name != "opportunities.db"
    assert "postgres" not in url

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv(
        "ENABLE_INTERNAL_SCHEDULER",
        "false",
    )

    try:
        yield path, url
    finally:
        if path.exists():
            path.unlink()


@pytest.fixture
def forbid_application_side_effect_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__
    forbidden = (
        "app.main",
        "app.internal_scheduler",
        "app.scanner",
    )

    def guarded_import(
        name: str,
        *args: object,
        **kwargs: object,
    ):
        if name.startswith(forbidden):
            raise AssertionError(
                f"Migration imported forbidden module: {name}"
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(
        builtins,
        "__import__",
        guarded_import,
    )


def _type_signature(column_type: sa.types.TypeEngine) -> tuple[object, ...]:
    if isinstance(column_type, sa.String):
        return ("string", column_type.length)
    if isinstance(column_type, sa.Text):
        return ("text",)
    if isinstance(column_type, sa.Integer):
        return ("integer",)
    if isinstance(column_type, sa.Float):
        return ("float",)
    if isinstance(column_type, sa.Numeric):
        return (
            "numeric",
            column_type.precision,
            column_type.scale,
        )
    if isinstance(column_type, sa.Boolean):
        return ("boolean",)
    if isinstance(column_type, sa.DateTime):
        return ("datetime",)
    if isinstance(column_type, sa.Date):
        return ("date",)
    raise AssertionError(
        f"Unsupported schema-comparison type: {column_type!r}"
    )


def _metadata_unique_columns(table: sa.Table) -> set[tuple[str, ...]]:
    constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    indexes = {
        tuple(column.name for column in index.columns)
        for index in table.indexes
        if index.unique
    }
    return constraints | indexes


def _database_unique_columns(
    inspector: sa.Inspector,
    table_name: str,
) -> set[tuple[str, ...]]:
    constraints = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints(table_name)
    }
    indexes = {
        tuple(item["column_names"])
        for item in inspector.get_indexes(table_name)
        if item["unique"]
    }
    return constraints | indexes


def _assert_schema_matches_metadata(
    inspector: sa.Inspector,
) -> None:
    assert set(inspector.get_table_names()) == (
        MANAGED_TABLES | {"alembic_version"}
    )

    for table_name in sorted(MANAGED_TABLES):
        table = Base.metadata.tables[table_name]
        reflected_columns = {
            column["name"]: column
            for column in inspector.get_columns(table_name)
        }
        assert set(reflected_columns) == {
            column.name for column in table.columns
        }

        for column in table.columns:
            reflected = reflected_columns[column.name]
            assert _type_signature(reflected["type"]) == (
                _type_signature(column.type)
            )
            assert reflected["nullable"] is column.nullable

        assert set(
            inspector.get_pk_constraint(table_name)[
                "constrained_columns"
            ]
        ) == {column.name for column in table.primary_key.columns}

        metadata_foreign_keys = {
            (
                tuple(constraint.column_keys),
                tuple(
                    element.target_fullname
                    for element in constraint.elements
                ),
                constraint.ondelete,
            )
            for constraint in table.foreign_key_constraints
        }
        database_foreign_keys = {
            (
                tuple(item["constrained_columns"]),
                tuple(
                    f"{item['referred_table']}.{column}"
                    for column in item["referred_columns"]
                ),
                item["options"].get("ondelete"),
            )
            for item in inspector.get_foreign_keys(table_name)
        }
        assert database_foreign_keys == metadata_foreign_keys
        assert _database_unique_columns(
            inspector,
            table_name,
        ) == _metadata_unique_columns(table)


def test_alembic_configuration_contains_no_database_url() -> None:
    contents = (PROJECT_ROOT / "alembic.ini").read_text(
        encoding="utf-8"
    )

    assert "sqlalchemy.url" not in contents
    assert "postgres://" not in contents
    assert "postgresql://" not in contents
    assert "opportunities.db" not in contents


def test_upgrade_downgrade_and_metadata_parity(
    migration_database: tuple[Path, str],
    forbid_application_side_effect_imports: None,
) -> None:
    path, url = migration_database
    configuration = _alembic_config()
    forbidden_modules_before = {
        name: sys.modules.get(name)
        for name in (
            "app.main",
            "app.internal_scheduler",
            "app.scanner",
        )
    }

    command.upgrade(configuration, "head")
    assert path.exists()

    engine = sa.create_engine(url)
    try:
        inspector = inspect(engine)
        _assert_schema_matches_metadata(inspector)

        with engine.connect() as connection:
            version = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert version == "20260731_01"

        command.upgrade(configuration, "head")
        _assert_schema_matches_metadata(inspect(engine))

        command.downgrade(configuration, "base")
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version"
        }
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).all() == []

        command.upgrade(configuration, "head")
        _assert_schema_matches_metadata(inspect(engine))
    finally:
        engine.dispose()

    assert {
        name: sys.modules.get(name)
        for name in forbidden_modules_before
    } == forbidden_modules_before
