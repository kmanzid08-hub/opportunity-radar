from __future__ import annotations

import importlib
import os

import pytest
import requests
from sqlalchemy import event


def test_application_starts_in_isolated_environment(client) -> None:
    from app import database, main
    from app.core.config import get_settings

    response = client.get("/")

    assert response.status_code == 200
    assert client.app.title == "Opportunity Radar"
    assert os.environ["ENABLE_INTERNAL_SCHEDULER"] == "false"
    assert os.environ["DATABASE_URL"] == "sqlite+pysqlite:///:memory:"
    assert database.DATABASE_URL == get_settings().database_url
    assert main.get_settings is get_settings
    assert get_settings().enable_internal_scheduler is False

    with pytest.raises(
        AssertionError,
        match="External network and subprocess access is blocked",
    ):
        requests.get("https://external.example.test", timeout=1)


def test_importing_application_does_not_access_database() -> None:
    from app import database, main

    statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(
        database.engine,
        "before_cursor_execute",
        record_statement,
    )
    try:
        importlib.reload(main)
    finally:
        event.remove(
            database.engine,
            "before_cursor_execute",
            record_statement,
        )

    assert statements == []


def test_import_and_startup_never_call_ai_or_discovery(monkeypatch, isolated_session_factory):
    from unittest.mock import Mock
    from fastapi.testclient import TestClient
    from app import main, source_discovery
    from app.intelligence.gemini_client import GeminiClient

    blocked = Mock(side_effect=AssertionError("No AI or discovery on import/startup"))
    monkeypatch.setattr(GeminiClient, "generate_structured", blocked)
    monkeypatch.setattr(GeminiClient, "generate_text", blocked)
    monkeypatch.setattr(source_discovery.SourceDiscovery, "run", blocked)
    statements = []
    engine = isolated_session_factory.kw["bind"]
    def record(_connection, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        importlib.reload(main)
        monkeypatch.setattr(main, "SessionLocal", isolated_session_factory)
        monkeypatch.setattr(main.scheduler, "start", blocked)
        with TestClient(main.app) as client:
            assert statements == []
            assert client.get("/", follow_redirects=False).status_code == 303
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert all(not sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for sql in statements)
    blocked.assert_not_called()
