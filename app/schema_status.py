from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import NullPool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANAGED_TABLES = frozenset(
    {
        "leads",
        "opportunities",
        "proposals",
        "sources",
    }
)


class SchemaState(str, Enum):
    EMPTY_DATABASE = "empty_database"
    CURRENT = "current"
    BEHIND = "behind"
    UNKNOWN_REVISION = "unknown_revision"
    NO_ALEMBIC_VERSION = "no_alembic_version"
    SCHEMA_DRIFT_SUSPECTED = "schema_drift_suspected"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    database_url: str
    dialect: str
    tables: tuple[str, ...]
    revision: str | None
    expected_revision: str
    state: SchemaState
    notes: tuple[str, ...]


def _migration_revisions() -> tuple[str, frozenset[str]]:
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(configuration)
    expected_revision = script.get_current_head()

    if expected_revision is None:
        raise RuntimeError("Alembic has no current head revision.")

    known_behind_revisions = frozenset(
        revision.revision
        for revision in script.walk_revisions(
            base="base",
            head=expected_revision,
        )
        if revision.revision != expected_revision
    )
    return expected_revision, known_behind_revisions


def _read_only_url(url: URL) -> tuple[URL, dict[str, object]]:
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            raise ValueError(
                "Schema inspection requires an existing SQLite file."
            )

        sqlite_url = url.set(
            database=f"file:{url.database}",
        ).update_query_dict(
            {
                "mode": "ro",
                "uri": "true",
            }
        )
        return sqlite_url, {}

    postgres_url = url
    if url.drivername == "postgresql":
        postgres_url = url.set(
            drivername="postgresql+psycopg"
        )

    return postgres_url, {
        "connect_args": {
            "options": "-c default_transaction_read_only=on"
        }
    }


def _status(
    *,
    database_url: str,
    dialect: str,
    tables: tuple[str, ...],
    revision: str | None,
    expected_revision: str,
    known_behind_revisions: frozenset[str],
    version_table_present: bool,
) -> SchemaStatus:
    managed_tables = MANAGED_TABLES.intersection(tables)

    if version_table_present:
        if revision == expected_revision:
            return SchemaStatus(
                database_url=database_url,
                dialect=dialect,
                tables=tables,
                revision=revision,
                expected_revision=expected_revision,
                state=SchemaState.CURRENT,
                notes=(
                    "The revision matches Alembic head; schema objects "
                    "are not compared by this inspection boundary.",
                ),
            )

        if revision in known_behind_revisions:
            return SchemaStatus(
                database_url=database_url,
                dialect=dialect,
                tables=tables,
                revision=revision,
                expected_revision=expected_revision,
                state=SchemaState.BEHIND,
                notes=(
                    "The revision is an ancestor of Alembic head.",
                ),
            )

        return SchemaStatus(
            database_url=database_url,
            dialect=dialect,
            tables=tables,
            revision=revision,
            expected_revision=expected_revision,
            state=SchemaState.UNKNOWN_REVISION,
            notes=(
                "The revision is not the current head or a known "
                "ancestor. With only the current baseline in the "
                "migration chain, an older schema cannot yet be "
                "distinguished from an unrelated revision.",
            ),
        )

    if not managed_tables:
        return SchemaStatus(
            database_url=database_url,
            dialect=dialect,
            tables=tables,
            revision=None,
            expected_revision=expected_revision,
            state=SchemaState.EMPTY_DATABASE,
            notes=("No Opportunity Radar ORM tables were found.",),
        )

    if managed_tables == MANAGED_TABLES:
        return SchemaStatus(
            database_url=database_url,
            dialect=dialect,
            tables=tables,
            revision=None,
            expected_revision=expected_revision,
            state=SchemaState.NO_ALEMBIC_VERSION,
            notes=(
                "All managed tables exist without Alembic revision "
                "tracking; compatibility has not been established.",
            ),
        )

    return SchemaStatus(
        database_url=database_url,
        dialect=dialect,
        tables=tables,
        revision=None,
        expected_revision=expected_revision,
        state=SchemaState.SCHEMA_DRIFT_SUSPECTED,
        notes=(
            "Only some Opportunity Radar ORM tables exist and no "
            "Alembic revision table was found.",
        ),
    )


def inspect_schema(database_url: str) -> SchemaStatus:
    """Inspect schema presence and revision without changing the database."""
    if not database_url or not database_url.strip():
        raise ValueError("database_url must be an explicit non-empty URL.")

    explicit_url = database_url.strip()
    expected_revision, known_behind_revisions = _migration_revisions()

    try:
        url = make_url(explicit_url)
    except ArgumentError as exc:
        raise ValueError("database_url is not a valid SQLAlchemy URL.") from exc

    dialect = url.get_backend_name()
    display_url = url.render_as_string(hide_password=True)

    if dialect not in {"sqlite", "postgresql"}:
        return SchemaStatus(
            database_url=display_url,
            dialect=dialect,
            tables=(),
            revision=None,
            expected_revision=expected_revision,
            state=SchemaState.UNSUPPORTED,
            notes=(
                f"Schema inspection does not support dialect {dialect!r}.",
            ),
        )

    read_only_url, engine_options = _read_only_url(url)
    engine = create_engine(
        read_only_url,
        poolclass=NullPool,
        **engine_options,
    )

    try:
        with engine.connect() as connection:
            table_names = tuple(
                sorted(inspect(connection).get_table_names())
            )
            version_table_present = "alembic_version" in table_names
            revision = None

            if version_table_present:
                revision_rows = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars().all()
                if len(revision_rows) == 1:
                    revision = str(revision_rows[0])
    finally:
        engine.dispose()

    return _status(
        database_url=display_url,
        dialect=dialect,
        tables=table_names,
        revision=revision,
        expected_revision=expected_revision,
        known_behind_revisions=known_behind_revisions,
        version_table_present=version_table_present,
    )
