from __future__ import annotations


def test_empty_pipeline_is_rendered(client) -> None:
    response = client.get("/pipeline")

    assert response.status_code == 200
    assert "Your pipeline is empty" in response.text
    assert "Browse opportunities" in response.text
