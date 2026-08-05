from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import source_discovery
from app.discovery.categories import (
    DISCOVERY_CATEGORIES,
    DiscoveryCategory,
    infer_discovery_tags,
)
from app.discovery.metadata import (
    decode_discovery_metadata,
    merge_discovery_metadata,
)
from app.models import Source
from app.source_discovery import SearchResult, SourceDiscovery
from app.source_quality import SourceQualityEvaluator


def _result(name: str) -> SearchResult:
    return SearchResult(
        title=name.title(),
        url=f"https://{name}.example.test",
        description=f"Result for {name}",
    )


def test_categories_are_complete_and_country_parameterized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    discovery = SourceDiscovery(country="Kenya")
    category_names = {
        category.name
        for category in discovery.DISCOVERY_CATEGORIES
    }

    assert category_names == {
        "Government procurement",
        "Government tenders",
        "NGO procurement",
        "NGO careers",
        "University procurement",
        "University careers",
        "Company careers",
        "Company procurement",
        "Consulting opportunities",
        "Vendor registration",
        "Grants",
        "Expressions of Interest",
        "Framework agreements",
        "Development partners",
        "International organisations",
        "Private sector procurement",
    }

    queries = tuple(
        query
        for category in discovery.DISCOVERY_CATEGORIES
        for query in category.queries_for(discovery.country)
    )

    assert queries
    assert all("Kenya" in query for query in queries)
    assert all("Rwanda" not in query for query in queries)


def test_discovery_score_uses_all_components_and_tags() -> None:
    evaluator = SourceQualityEvaluator()
    category = DISCOVERY_CATEGORIES[0]
    assessment = evaluator.assess(
        title="Ministry Procurement Tenders",
        description=(
            "Government authority request for proposal in Kenya."
        ),
        url="https://procurement.example.gov.ke/tenders/",
        discovery_query="Kenya government procurement opportunities",
        country="Kenya",
    )
    tags = infer_discovery_tags(
        category=category,
        title="Ministry Procurement Tenders",
        description="Government authority procurement notice",
        url=assessment.monitor_url,
        source_type=assessment.source_type,
    )

    assert assessment.accepted is True
    assert assessment.discovery_score == assessment.priority_score
    assert set(assessment.score_components) == {
        "domain_quality",
        "url_relevance",
        "keyword_relevance",
        "page_title",
        "organisation_confidence",
    }
    assert all(
        score > 0
        for score in assessment.score_components.values()
    )
    assert {"government", "procurement"}.issubset(tags)


def test_metadata_merges_legacy_and_new_discoveries() -> None:
    encoded = merge_discovery_metadata(
        "legacy procurement query",
        categories=("Government procurement",),
        queries=("second procurement query",),
        tags=("government", "procurement"),
    )
    metadata = decode_discovery_metadata(encoded)

    assert metadata.categories == ("Government procurement",)
    assert metadata.queries == (
        "legacy procurement query",
        "second procurement query",
    )
    assert metadata.tags == ("government", "procurement")


def test_normalized_duplicate_merges_queries_and_tags(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    monkeypatch.setattr(
        source_discovery,
        "SessionLocal",
        isolated_session_factory,
    )
    discovery = SourceDiscovery(country="Kenya")

    first = {
        "organisation_name": "Example Authority",
        "base_url": "https://www.example.test",
        "monitor_url": (
            "HTTPS://WWW.EXAMPLE.TEST/procurement/?utm_source=search"
        ),
        "domain": "example.test",
        "source_type": "Government Institution",
        "discovered_from": "https://search.example.test/first",
        "discovery_query": "first query",
        "discovery_category": "Government procurement",
        "tags": ("government", "procurement"),
        "discovery_score": 70.0,
        "confidence_score": 70.0,
        "priority_score": 70.0,
        "url_relevance_score": 60.0,
    }
    duplicate = {
        **first,
        "monitor_url": "https://www.example.test/procurement/",
        "discovery_query": "second query",
        "discovery_category": "Government tenders",
        "tags": ("government", "procurement", "audit"),
        "discovery_score": 80.0,
        "confidence_score": 80.0,
        "priority_score": 80.0,
    }

    assert discovery._save_candidate(first) is True
    assert discovery._save_candidate(duplicate) is False

    with isolated_session_factory() as session:
        sources = session.scalars(select(Source)).all()

    assert len(sources) == 1
    assert sources[0].monitor_url == (
        "https://www.example.test/procurement"
    )
    assert sources[0].priority_score == 80.0

    metadata = decode_discovery_metadata(
        sources[0].discovery_query
    )
    assert metadata.categories == (
        "Government procurement",
        "Government tenders",
    )
    assert metadata.queries == ("first query", "second query")
    assert metadata.tags == ("audit", "government", "procurement")


def test_discovery_reports_category_and_total_metrics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    discovery = SourceDiscovery(country="Kenya")
    categories = (
        DiscoveryCategory(
            name="Procurement",
            query_templates=("first query", "second query"),
            tags=("procurement",),
        ),
        DiscoveryCategory(
            name="Careers",
            query_templates=("failed query",),
            tags=("career",),
        ),
    )
    monkeypatch.setattr(discovery, "DISCOVERY_CATEGORIES", categories)
    monkeypatch.setattr(discovery, "REQUEST_DELAY_SECONDS", 1.0)
    waits: list[None] = []
    monkeypatch.setattr(
        discovery,
        "_wait",
        lambda: waits.append(None),
    )
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
        category: DiscoveryCategory,
    ) -> dict[str, object] | None:
        del query, category
        if "rejected" in result.url:
            return None
        return {
            "organisation_name": result.title,
            "monitor_url": result.url,
            "discovery_score": 80.0,
            "tags": ("procurement",),
        }

    monkeypatch.setattr(discovery, "_search", search)
    monkeypatch.setattr(discovery, "_build_candidate", build_candidate)
    monkeypatch.setattr(
        discovery,
        "_save_candidate",
        lambda candidate: "new" in str(candidate["monitor_url"]),
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
        "configured_delay_seconds": 2.0,
        "total_runtime_seconds": 4.0,
        "average_runtime_seconds": pytest.approx(
            0.2833333333333333
        ),
        "categories_processed": 2,
    }
    assert len(waits) == 2

    output = capsys.readouterr().out
    assert "Category: Procurement" in output
    assert "Category: Careers" in output
    assert "  Queries executed: 2" in output
    assert "  Results: 3" in output
    assert "  Accepted: 2" in output
    assert "  Duplicates: 1" in output
    assert "  Rejected: 1" in output
    assert "Total queries:                 3" in output
    assert "Total results:                 3" in output
    assert "New sources:                   1" in output
    assert "Average runtime:               0.283 seconds" in output
    assert "Total runtime:                 4.000 seconds" in output
