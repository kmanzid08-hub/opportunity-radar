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

    scheduler_start = Mock()
    scheduler_stop = Mock()
    monkeypatch.setattr(main.scheduler, "start", scheduler_start)
    monkeypatch.setattr(main.scheduler, "stop", scheduler_stop)

    with TestClient(main.app) as test_client:
        yield test_client

    scheduler_start.assert_not_called()
    scheduler_stop.assert_not_called()
