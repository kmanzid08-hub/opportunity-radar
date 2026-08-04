from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import source_discovery
from app.source_discovery import (
    RwandaSourceDiscovery,
    SearchResult,
)


def _result(name: str) -> SearchResult:
    return SearchResult(
        title=name.title(),
        url=f"https://{name}.example.test",
        description=f"Result for {name}",
    )


def test_discovery_reports_per_query_and_total_metrics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    discovery = RwandaSourceDiscovery()
    monkeypatch.setattr(
        discovery,
        "SEARCH_QUERIES",
        ("first query", "second query", "failed query"),
    )
    monkeypatch.setattr(discovery, "MAX_QUERIES_PER_RUN", 3)
    monkeypatch.setattr(discovery, "REQUEST_DELAY_SECONDS", 1.0)
    monkeypatch.setattr(discovery, "_wait", lambda: None)
    monkeypatch.setattr(
        source_discovery,
        "Base",
        SimpleNamespace(
            metadata=SimpleNamespace(
                create_all=lambda **_kwargs: None
            )
        ),
    )

    search_results = {
        "first query": [_result("new"), _result("rejected")],
        "second query": [_result("duplicate")],
    }

    def search(query: str) -> list[SearchResult]:
        if query == "failed query":
            raise RuntimeError("provider unavailable")
        return search_results[query]

    def build_candidate(
        *,
        result: SearchResult,
        query: str,
    ) -> dict[str, object] | None:
        del query
        if "rejected" in result.url:
            return None
        return {
            "organisation_name": result.title,
            "monitor_url": result.url,
            "confidence_score": 80.0,
        }

    monkeypatch.setattr(discovery, "_search", search)
    monkeypatch.setattr(
        discovery,
        "_build_candidate",
        build_candidate,
    )
    monkeypatch.setattr(
        discovery,
        "_save_candidate",
        lambda candidate: "new" in str(
            candidate["monitor_url"]
        ),
    )

    clock_values = iter(
        (0.0, 1.0, 1.25, 2.0, 2.5, 3.0, 3.1, 4.0)
    )
    monkeypatch.setattr(
        source_discovery.time,
        "perf_counter",
        lambda: next(clock_values),
    )

    statistics = discovery.run()

    assert statistics == {
        "queries_executed": 3,
        "total_results": 3,
        "candidate_results": 2,
        "added_sources": 1,
        "existing_sources": 1,
        "rejected_results": 1,
        "failed_queries": 1,
        "configured_delay_seconds": 3.0,
        "total_runtime_seconds": 4.0,
    }

    output = capsys.readouterr().out
    assert "[QUERY 1/3] first query" in output
    assert "[QUERY 2/3] second query" in output
    assert "[QUERY 3/3] failed query" in output
    assert "Results returned: 2" in output
    assert "New sources accepted: 1" in output
    assert "Duplicates: 1" in output
    assert "Query runtime: 0.250 seconds" in output
    assert "Query runtime: 0.500 seconds" in output
    assert "Query status: FAILED" in output
    assert "Queries executed:              3" in output
    assert "Configured query delay:        3.000 seconds" in output
    assert "Total runtime:                 4.000 seconds" in output
