from __future__ import annotations

import os
import socket
import subprocess
import sys
from unittest.mock import Mock
import urllib.request

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# These values must be established before any application module is imported.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ENABLE_INTERNAL_SCHEDULER"] = "false"
os.environ["TENDER_SCAN_COMMAND"] = "test-disabled"
os.environ["SOURCE_DISCOVERY_COMMAND"] = "test-disabled"
os.environ.pop("BRAVE_API_KEY", None)
os.environ.pop("BRAVE_SEARCH_API_KEY", None)


class ExternalAccessBlocked(AssertionError):
    """Raised when a test attempts external I/O."""


ORIGINAL_SOCKET_CONNECT = socket.socket.connect


def stamp_current_fixture_matches(db) -> None:
    """Explicit opt-in for legacy UI fixtures, never production or autouse.

    Rendering tests now need real analysis/profile fingerprints instead of dummy
    hashes. Preserve their chosen scores/filtering data and use the real builder.
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models import Opportunity, AIOpportunityAnalysis, CompanyOpportunityMatch
    from app.intelligence.schemas import OpportunityAnalysis
    from app.intelligence.opportunity_pipeline import load_canonical_analyses
    from app.intelligence.match_store import load_current_profiles
    from app.intelligence.matching_engine import build_match_fingerprint

    db.flush()
    matches = list(db.scalars(select(CompanyOpportunityMatch)))
    analyses = load_canonical_analyses(db, {m.opportunity_id for m in matches})
    for oid in {m.opportunity_id for m in matches} - analyses.keys():
        opportunity = db.get(Opportunity, oid)
        analysis = OpportunityAnalysis(
            decision='ACCEPT', confidence=95, reason='Stored fixture analysis',
            title=opportunity.title, buyer_name=opportunity.organisation_name,
            opportunity_type='CONSULTANCY', status='ACTIVE',
            summary=opportunity.description, deadline=opportunity.deadline,
        )
        db.add(AIOpportunityAnalysis(
            opportunity_id=oid, source_url=opportunity.source_url,
            raw_title=opportunity.title, source_name=opportunity.source_name,
            content_fingerprint='fixture-content', analysis_data=analysis.model_dump(mode='json'),
            analyzed_at=datetime.now(timezone.utc),
        ))
        analyses[oid] = analysis
    profiles = load_current_profiles(db)
    for match in matches:
        profile = profiles[match.organization_id]
        match.match_fingerprint = build_match_fingerprint(
            company_profile=profile.profile, opportunity=analyses[match.opportunity_id],
            minimum_score=profile.minimum_score,
        )


def _block_external_access(*_args: object, **_kwargs: object) -> None:
    raise ExternalAccessBlocked(
        "External network and subprocess access is blocked during tests."
    )


def _guard_socket_connect(
    sock: socket.socket,
    address: object,
) -> object:
    if (
        isinstance(address, tuple)
        and address
        and address[0] in {"127.0.0.1", "::1", "localhost"}
    ):
        return ORIGINAL_SOCKET_CONNECT(sock, address)

    _block_external_access(sock, address)


@pytest.fixture(scope="session", autouse=True)
def clean_import_database() -> None:
    yield

    database_module = sys.modules.get("app.database")
    if database_module is not None:
        database_module.engine.dispose()


@pytest.fixture(autouse=True)
def block_external_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        requests.sessions.Session,
        "request",
        _block_external_access,
    )
    monkeypatch.setattr(
        requests.api,
        "request",
        _block_external_access,
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _block_external_access,
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        _block_external_access,
    )
    monkeypatch.setattr(
        socket.socket,
        "connect",
        _guard_socket_connect,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _block_external_access,
    )


@pytest.fixture
def isolated_session_factory(
    monkeypatch: pytest.MonkeyPatch,
):
    from app import database
    from app.database import Base
    from app import main
    from app import models  # noqa: F401

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(
        database,
        "SessionLocal",
        testing_session_factory,
    )
    monkeypatch.setattr(
        main,
        "SessionLocal",
        testing_session_factory,
    )

    yield testing_session_factory

    engine.dispose()


@pytest.fixture
def client(
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
):
    from app import main

    # Existing presentation/characterization tests deliberately exercise the legacy
    # unscoped fixture data. Security and beta journeys use real TestClient sessions
    # in test_private_beta.py, without this narrowly scoped fixture override.
    from app.beta_security import BetaSecurityMiddleware

    async def legacy_dispatch(self, request, call_next):
        request.state.csrf = "legacy-test-only"
        request.state.user = None
        return await call_next(request)

    monkeypatch.setattr(BetaSecurityMiddleware, "dispatch", legacy_dispatch)

    scheduler_start = Mock()
    scheduler_stop = Mock()
    monkeypatch.setattr(main.scheduler, "start", scheduler_start)
    monkeypatch.setattr(main.scheduler, "stop", scheduler_stop)

    main.app.middleware_stack = None
    with TestClient(main.app) as test_client:
        yield test_client

    main.app.middleware_stack = None
    scheduler_start.assert_not_called()
    scheduler_stop.assert_not_called()
