from __future__ import annotations


def test_empty_dashboard_is_rendered(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Opportunity Radar" in response.text
    assert "No opportunities found" in response.text
