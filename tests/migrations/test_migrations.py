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
BETA_TABLES = {"workspaces", "beta_users", "beta_sessions", "beta_usage", "workspace_opportunities"}
MANAGED_TABLES = BETA_TABLES | {
    "ai_opportunity_analysis",
    "ai_opportunity_verification",
    "automation_job_state",
    "company_opportunity_matches",
    "company_opportunity_triage",
    "company_opportunity_readiness",
    "opportunity_requirements",
    "opportunity_requirement_extractions",
    "leads",
    "opportunities",
    "opportunity_preferences",
    "organizations",
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
    if isinstance(column_type, sa.JSON):
        return ("json",)
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
        assert version == "20261030_12"

        command.upgrade(configuration, "head")
        _assert_schema_matches_metadata(inspect(engine))

        with pytest.raises(RuntimeError, match="forward-only"):
            command.downgrade(configuration, "base")
        _assert_schema_matches_metadata(inspect(engine))
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "20261030_12"

        command.upgrade(configuration, "head")
        _assert_schema_matches_metadata(inspect(engine))
    finally:
        engine.dispose()

    assert {
        name: sys.modules.get(name)
        for name in forbidden_modules_before
    } == forbidden_modules_before


def test_rich_profile_upgrade_preserves_existing_company_data(migration_database):
    from sqlalchemy.orm import Session
    from app.models import OpportunityPreference, Organization
    from app.intelligence.matching_engine import build_company_profile

    _, url = migration_database
    configuration = _alembic_config()
    command.upgrade(configuration, "20260916_06")
    engine = sa.create_engine(url)
    try:
        metadata = sa.MetaData()
        organizations = sa.Table("organizations", metadata, autoload_with=engine)
        preferences = sa.Table(
            "opportunity_preferences", metadata, autoload_with=engine
        )
        with engine.begin() as connection:
            connection.execute(organizations.insert().values(
                id=1, name="Existing Advisory", description="Existing evidence",
            ))
            connection.execute(preferences.insert().values(
                organization_id=1, countries=["Kenya"], regions=["East Africa"],
                industries=["Agriculture"], services=["Evaluation"],
                opportunity_types=["RFP"], keywords=["Public programmes"],
                languages=["English"], minimum_match_score=81,
            ))
        command.upgrade(configuration, "head")
        with Session(engine) as db:
            company = db.get(Organization, 1)
            preference = db.scalar(sa.select(OpportunityPreference))
            assert company.description == "Existing evidence"
            assert preference.services == ["Evaluation"]
            assert preference.countries == ["Kenya"]
            assert preference.minimum_match_score == 81
            assert preference.intelligence == {}
            profile = build_company_profile(company, preference)
            assert profile.profile["capabilities"] == []
            assert profile.profile["experience"]["relevant_experience"] == []
            preference.intelligence = {"capabilities": ["Field research"]}
            db.commit()
        with Session(engine) as db:
            assert db.scalar(sa.select(OpportunityPreference)).intelligence == {
                "capabilities": ["Field research"]
            }
    finally:
        engine.dispose()


def test_rich_profile_migration_compiles_for_postgresql_without_connection(
    monkeypatch,
):
    from io import StringIO

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused/unused")
    output = StringIO()
    configuration = _alembic_config()
    configuration.output_buffer = output
    command.upgrade(configuration, "20260916_06:20260924_07", sql=True)
    sql = output.getvalue()
    assert "ADD COLUMN intelligence JSON DEFAULT '{}' NOT NULL" in sql


def test_triage_migration_compiles_for_postgresql_without_connection(monkeypatch):
    from io import StringIO

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused/unused")
    output = StringIO()
    configuration = _alembic_config()
    configuration.output_buffer = output
    command.upgrade(configuration, "20260924_07:20260925_08", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE company_opportunity_triage" in sql
    assert "result_data JSON" in sql
    assert "CONSTRAINT uq_company_opportunity_triage UNIQUE (organization_id, opportunity_id)" in sql
    assert "REFERENCES organizations (id) ON DELETE CASCADE" in sql
    assert "REFERENCES opportunities (id) ON DELETE CASCADE" in sql


def test_readiness_migration_postgresql_sql_without_connection(monkeypatch):
    from io import StringIO

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused/unused")
    output = StringIO()
    configuration = _alembic_config()
    configuration.output_buffer = output
    command.upgrade(configuration, "20260925_08:20260925_09", sql=True)
    sql = output.getvalue()
    for table in ("opportunity_requirements", "company_opportunity_readiness", "opportunity_requirement_extractions"):
        assert f"CREATE TABLE {table}" in sql
    assert "uq_company_opportunity_requirement UNIQUE (company_id, opportunity_id, requirement_key)" in sql
    assert "document_ids JSON NOT NULL" in sql


def test_readiness_upgrade_preserves_opportunities_and_triage(migration_database):
    _, url = migration_database
    configuration = _alembic_config()
    command.upgrade(configuration, "20260925_08")
    engine = sa.create_engine(url)
    try:
        metadata = sa.MetaData()
        organizations = sa.Table("organizations", metadata, autoload_with=engine)
        opportunities = sa.Table("opportunities", metadata, autoload_with=engine)
        triage = sa.Table("company_opportunity_triage", metadata, autoload_with=engine)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        with engine.begin() as db:
            db.execute(organizations.insert().values(id=1, name="Preserved Company"))
            db.execute(opportunities.insert().values(
                id=1, title="Preserved opportunity", source_url="https://preserve.test/opportunity",
                source_name="Test portal", category="Consultancy",
            ))
            db.execute(triage.insert().values(
                organization_id=1, opportunity_id=1, triage_fingerprint="a" * 64,
                match_fingerprint="b" * 64, effective_status="ACTIVE",
                assessment_date=now.date(), assessed_at=now, result_data={"preserved": True},
            ))
        command.upgrade(configuration, "20260925_09")
        with engine.connect() as db:
            assert db.scalar(sa.select(organizations.c.name)) == "Preserved Company"
            assert db.scalar(sa.select(opportunities.c.title)) == "Preserved opportunity"
            assert db.scalar(sa.select(triage.c.result_data)) == {"preserved": True}
        command.downgrade(configuration, "20260925_08")
        with engine.connect() as db:
            assert db.scalar(sa.select(opportunities.c.title)) == "Preserved opportunity"
    finally:
        engine.dispose()


def test_applicant_eligibility_upgrade_preserves_current_matches(migration_database):
    from datetime import datetime, timezone

    _, url = migration_database
    configuration = _alembic_config()
    command.upgrade(configuration, "20260930_10")
    engine = sa.create_engine(url)
    try:
        metadata = sa.MetaData()
        companies = sa.Table("organizations", metadata, autoload_with=engine)
        opportunities = sa.Table("opportunities", metadata, autoload_with=engine)
        matches = sa.Table("company_opportunity_matches", metadata, autoload_with=engine)
        with engine.begin() as db:
            db.execute(companies.insert().values(id=1, name="Preserved company"))
            db.execute(opportunities.insert().values(id=1, title="Preserved notice", source_url="https://example.test/notice", source_name="Example", category="RFP"))
            db.execute(matches.insert().values(organization_id=1, opportunity_id=1, score=92, match_strength="VERY_STRONG", strengths=[], potential_gaps=[], eligibility_concerns=[], minimum_score=70, qualifies_for_feed=True, match_fingerprint="a"*64, matched_at=datetime.now(timezone.utc)))
        command.upgrade(configuration, "head")
        command.upgrade(configuration, "head")
        with engine.connect() as db:
            assert db.execute(sa.text("SELECT score, applicant_eligibility FROM company_opportunity_matches")).one() == (92, None)
            assert db.scalar(sa.select(companies.c.name)) == "Preserved company"
        _assert_schema_matches_metadata(inspect(engine))
    finally:
        engine.dispose()


def test_complete_postgresql_chain_generates_sql_without_connection(monkeypatch):
    from io import StringIO
    from alembic.script import ScriptDirectory

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused/unused")
    configuration = _alembic_config()
    assert ScriptDirectory.from_config(configuration).get_heads() == ["20261030_12"]
    output = StringIO()
    configuration.output_buffer = output
    command.upgrade(configuration, "base:head", sql=True)
    sql = output.getvalue()
    for table in MANAGED_TABLES:
        assert f"CREATE TABLE {table}" in sql
    assert "20260930_10" in sql
    assert "ALTER TABLE opportunities ADD COLUMN original_source_url TEXT" in sql
    assert "ALTER TABLE sources ADD COLUMN source_role" in sql
    assert "ALTER TABLE company_opportunity_matches ADD COLUMN applicant_eligibility VARCHAR(20)" in sql
    output = StringIO()
    configuration.output_buffer = output
    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(configuration, "head:base", sql=True)
    command.downgrade(configuration, "20260925_09:base", sql=True)
    for table in MANAGED_TABLES - BETA_TABLES:
        assert f"DROP TABLE {table}" in output.getvalue()


def test_provenance_upgrade_preserves_legacy_data_without_inventing_geography(migration_database):
    from datetime import datetime, timezone

    _, url = migration_database
    configuration = _alembic_config()
    command.upgrade(configuration, "20260925_09")
    engine = sa.create_engine(url)
    try:
        metadata = sa.MetaData()
        opportunities = sa.Table("opportunities", metadata, autoload_with=engine)
        sources = sa.Table("sources", metadata, autoload_with=engine)
        analyses = sa.Table("ai_opportunity_analysis", metadata, autoload_with=engine)
        old_data = {"country": "Rwanda", "original_source_url": "https://portal.test/"}
        with engine.begin() as db:
            db.execute(opportunities.insert().values(id=1, title="Preserved notice", category="RFP", source_name="Portal", source_url="https://portal.test/"))
            db.execute(sources.insert().values(base_url="https://portal.test/", monitor_url="https://portal.test/", domain="portal.test"))
            db.execute(analyses.insert().values(opportunity_id=1, source_url="https://portal.test/", raw_title="Notice", source_name="Portal", content_fingerprint="a"*64, analyzed_at=datetime.now(timezone.utc), analysis_data=old_data))
        command.upgrade(configuration, "head")
        with engine.connect() as db:
            assert db.execute(sa.text("SELECT source_url, original_source_url FROM opportunities")).one() == ("https://portal.test/", None)
            assert db.scalar(sa.text("SELECT source_role FROM sources")) == "UNKNOWN"
            assert db.scalar(sa.select(analyses.c.analysis_data)) == old_data
        command.upgrade(configuration, "head")
        _assert_schema_matches_metadata(inspect(engine))
    finally:
        engine.dispose()
