from __future__ import annotations

import os

import pytest
import requests


def test_application_starts_in_isolated_environment(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert client.app.title == "Opportunity Radar"
    assert os.environ["ENABLE_INTERNAL_SCHEDULER"] == "false"
    assert os.environ["DATABASE_URL"] == "sqlite+pysqlite:///:memory:"

    with pytest.raises(
        AssertionError,
        match="External network and subprocess access is blocked",
    ):
        requests.get("https://external.example.test", timeout=1)
