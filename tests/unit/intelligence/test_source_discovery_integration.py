from __future__ import annotations

from collections.abc import Iterable

import pytest
from sqlalchemy import select

from app import source_discovery
from app.discovery.categories import DISCOVERY_CATEGORIES
from app.intelligence.gemini_client import GeminiAPIError, GeminiResponseError
from app.intelligence.schemas import SourceAnalysis
from app.models import Source
from app.source_discovery import SearchResult, SourceDiscovery


class RecordingAnalyzer:
    def __init__(self, outcomes: Iterable[object]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ContentSequence:
    def __init__(self, *contents: str) -> None:
        self.contents = iter(contents)
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        return next(self.contents)


def _analysis(
    decision: str,
    *,
    confidence: int = 90,
    country: str | None = "Rwanda",
    reason: str = "Evidence-based source classification.",
) -> SourceAnalysis:
    return SourceAnalysis(
        decision=decision,
        confidence=confidence,
        source_name="Example Procurement Authority",
        organisation="Example Procurement Authority",
        country=country,
        source_type="Government procurement portal",
        is_official=True,
        publishes_opportunities=True,
        reason=reason,
    )


def _candidate(
    *,
    monitor_url: str = "https://procurement.example.test/notices",
    country: str = "Rwanda",
) -> dict[str, object]:
    return {
        "url": monitor_url,
        "organisation_name": "Example Authority",
        "base_url": "https://procurement.example.test",
        "monitor_url": monitor_url,
        "domain": "procurement.example.test",
        "page_title": "Procurement notices",
        "description": "Official notices from the authority.",
        "country_context": country,
        "discovery_method": "Brave Search API",
        "source_type": "Government Institution",
        "discovered_from": monitor_url,
        "discovery_query": f"{country} procurement opportunities",
        "discovery_category": "Government procurement",
        "tags": ("government", "procurement"),
        "discovery_score": 70.0,
        "confidence_score": 70.0,
        "priority_score": 70.0,
        "url_relevance_score": 60.0,
    }


def _discovery(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
    *,
    analyzer: RecordingAnalyzer,
    content_fetcher: ContentSequence,
    country: str = "Rwanda",
) -> SourceDiscovery:
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    monkeypatch.setattr(
        source_discovery,
        "SessionLocal",
        isolated_session_factory,
    )
    return SourceDiscovery(
        country=country,
        source_analyzer=analyzer,
        content_fetcher=content_fetcher,
    )


@pytest.mark.parametrize("failures", [1, 3])
def test_client_retries_preserve_durable_success_and_error_cache_semantics(
    monkeypatch, isolated_session_factory, failures
):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from google.genai.errors import APIError
    from app.intelligence.gemini_client import GeminiClient, GeminiConfig
    from app.intelligence.source_analyzer import analyze_source

    outcomes = iter(
        [
            *[
                APIError(503, {"message": "Temporary provider failure"})
                for _ in range(failures)
            ],
            SimpleNamespace(parsed=_analysis("ACCEPT").model_dump()),
        ]
    )

    def generate(**_kwargs):
        # No failed intermediate attempt can have stored a successful fingerprint.
        with isolated_session_factory() as db:
            source = db.scalar(select(Source))
            assert source is None or source.source_fingerprint is None
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    sdk = SimpleNamespace(
        models=SimpleNamespace(generate_content=Mock(side_effect=generate))
    )
    client = GeminiClient(
        GeminiConfig(api_key="test-key"),
        client_factory=lambda **_: sdk,
        sleep=Mock(),
        jitter=lambda: 0,
    )
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=lambda **kwargs: analyze_source(client=client, **kwargs),
        content_fetcher=ContentSequence(
            *(["Stable official procurement content."] * 3)
        ),
    )
    try:
        first = discovery._save_candidate(_candidate())
        if failures == 3:
            assert first.status == "ERROR"
            with isolated_session_factory() as db:
                source = db.scalar(select(Source))
                assert source.ai_status == "ERROR"
                assert source.source_fingerprint is None
                assert not source.is_approved
            # A later operation with unchanged content retries an error, not a success cache.
            assert discovery._save_candidate(_candidate()).status == "ACCEPT"
        else:
            assert first.status == "ACCEPT"
        cached = discovery._save_candidate(_candidate())
        assert cached.status == "ACCEPT"
        assert not cached.analyzed
        assert sdk.models.generate_content.call_count == failures + 1
        with isolated_session_factory() as db:
            source = db.scalar(select(Source))
            assert source.source_fingerprint
            assert source.ai_status == "ACCEPT"
    finally:
        discovery.session.close()
        discovery.content_session.close()


def test_new_search_result_builds_unapproved_ai_candidate(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=RecordingAnalyzer([_analysis("ACCEPT")]),
        content_fetcher=ContentSequence("Page content"),
    )

    candidate = discovery._build_candidate(
        result=SearchResult(
            title="Example Authority",
            url="https://agency.example.test/about",
            description="Official institutional website.",
        ),
        query="Rwanda procurement sources",
        category=DISCOVERY_CATEGORIES[0],
    )

    assert candidate is not None
    assert {
        "url",
        "domain",
        "page_title",
        "description",
        "country_context",
        "discovery_method",
    }.issubset(candidate)
    assert candidate["is_active"] is False
    assert candidate["is_approved"] is False


def test_accepted_source_is_activated_and_persisted(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    analyzer = RecordingAnalyzer([_analysis("ACCEPT", confidence=96)])
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=analyzer,
        content_fetcher=ContentSequence("Official current procurement notices."),
    )

    outcome = discovery._save_candidate(_candidate())

    assert outcome.status == "ACCEPT"
    assert outcome.created is True
    assert outcome.analyzed is True
    with isolated_session_factory() as db:
        source = db.scalar(select(Source))
        assert source is not None
        assert source.is_active is True
        assert source.is_approved is True
        assert source.ai_status == "ACCEPT"
        assert source.ai_confidence == 96
        assert source.ai_source_type == "Government procurement portal"
        assert source.ai_reason == "Evidence-based source classification."
        assert source.ai_analyzed_at is not None
        assert source.source_fingerprint is not None
        assert len(source.source_fingerprint) == 64


@pytest.mark.parametrize("decision", ["REJECT", "REVIEW"])
def test_nonaccepted_source_remains_inactive(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
    decision: str,
) -> None:
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=RecordingAnalyzer([_analysis(decision)]),
        content_fetcher=ContentSequence("Ambiguous source page."),
    )

    outcome = discovery._save_candidate(_candidate())

    assert outcome.status == decision
    with isolated_session_factory() as db:
        source = db.scalar(select(Source))
        assert source is not None
        assert source.ai_status == decision
        assert source.is_active is False
        assert source.is_approved is False


def test_country_mismatch_is_passed_to_ai_and_rejected(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    analyzer = RecordingAnalyzer(
        [
            _analysis(
                "REJECT",
                country="Kenya",
                reason="This source serves Kenya, not the target country.",
            )
        ]
    )
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=analyzer,
        content_fetcher=ContentSequence("Kenyan procurement authority notices."),
    )

    outcome = discovery._save_candidate(_candidate(country="Rwanda"))

    assert outcome.status == "REJECT"
    assert analyzer.calls[0]["country_hint"] == "Rwanda"
    with isolated_session_factory() as db:
        source = db.scalar(select(Source))
        assert source is not None
        assert source.ai_reason == ("This source serves Kenya, not the target country.")


def test_duplicate_unchanged_source_reuses_cached_analysis(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    analyzer = RecordingAnalyzer([_analysis("ACCEPT")])
    content = "Stable official procurement content."
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=analyzer,
        content_fetcher=ContentSequence(content, f"  {content}  "),
    )

    first = discovery._save_candidate(_candidate())
    duplicate = discovery._save_candidate(_candidate())

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.analyzed is False
    assert duplicate.status == "ACCEPT"
    assert len(analyzer.calls) == 1


def test_changed_source_content_triggers_reanalysis(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    analyzer = RecordingAnalyzer(
        [
            _analysis("ACCEPT"),
            _analysis(
                "REJECT",
                reason="The site is now a marketing page.",
            ),
        ]
    )
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=analyzer,
        content_fetcher=ContentSequence(
            "Official procurement content.",
            "Consulting company marketing services.",
        ),
    )

    first = discovery._save_candidate(_candidate())
    changed = discovery._save_candidate(_candidate())

    assert first.status == "ACCEPT"
    assert changed.status == "REJECT"
    assert changed.created is False
    assert changed.analyzed is True
    assert len(analyzer.calls) == 2
    with isolated_session_factory() as db:
        source = db.scalar(select(Source))
        assert source is not None
        assert source.is_active is False
        assert source.ai_reason == "The site is now a marketing page."


@pytest.mark.parametrize(
    "failure",
    [
        GeminiAPIError("Gemini generation failed."),
        GeminiResponseError("Gemini returned an invalid structured response."),
    ],
    ids=["api-failure", "malformed-response"],
)
def test_gemini_failure_is_persisted_without_caching(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
    failure: Exception,
) -> None:
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=RecordingAnalyzer([failure]),
        content_fetcher=ContentSequence("Candidate source content."),
    )

    outcome = discovery._save_candidate(_candidate())

    assert outcome.status == "ERROR"
    with isolated_session_factory() as db:
        source = db.scalar(select(Source))
        assert source is not None
        assert source.ai_status == "ERROR"
        assert source.ai_reason == "Gemini source analysis failed."
        assert source.source_fingerprint is None
        assert source.is_active is False
        assert source.is_approved is False


def test_multiple_monitor_paths_on_same_domain_are_preserved_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    """Procurement and careers pages must not replace each other."""
    analyzer = RecordingAnalyzer([_analysis("ACCEPT") for _ in range(4)])
    fetcher = ContentSequence(
        *[f"Legitimate Rwanda opportunity page number {i}" for i in range(4)],
        "Legitimate Rwanda opportunity page number 0",
    )
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=analyzer,
        content_fetcher=fetcher,
    )

    paths = ["/procurement", "/consultancies", "/careers", "/tenders"]
    for path in paths:
        outcome = discovery._save_candidate(
            _candidate(monitor_url="https://procurement.example.test" + path)
        )
        assert outcome.status == "ACCEPT"
        assert outcome.created

    # A fifth same-domain URL must not cause unbounded API/crawl growth.
    overflow = discovery._save_candidate(
        _candidate(monitor_url="https://procurement.example.test/jobs")
    )
    assert overflow.status == "DOMAIN_CAP"
    assert overflow.analyzed is False
    assert len(fetcher.calls) == 4
    assert len(analyzer.calls) == 4

    with isolated_session_factory() as db:
        stored = db.scalars(select(Source).order_by(Source.monitor_url)).all()
        assert len(stored) == 4
        assert len({source.domain for source in stored}) == 1
        assert all(source.is_approved for source in stored)
        assert {source.monitor_url for source in stored} == {
            "https://procurement.example.test" + path for path in paths
        }

    # Repeated discovery of an existing path still uses its cached source.
    cached = discovery._save_candidate(
        _candidate(monitor_url="https://procurement.example.test/procurement")
    )
    assert cached.status == "ACCEPT"
    assert not cached.created
    assert not cached.analyzed
