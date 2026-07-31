from __future__ import annotations

import os

import pytest
import requests


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
