from __future__ import annotations


def test_empty_pipeline_is_rendered(client) -> None:
    response = client.get("/pipeline")

    assert response.status_code == 200
    assert "Your Pipeline is empty" in response.text
    assert "Browse Opportunities" in response.text
