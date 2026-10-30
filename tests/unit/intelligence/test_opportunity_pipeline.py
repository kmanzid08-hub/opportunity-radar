from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from hashlib import sha256

import pytest
from sqlalchemy import func, select

from app.intelligence import opportunity_pipeline
from app.intelligence.gemini_client import GeminiAPIError
from app.intelligence.opportunity_pipeline import (
    OpportunityProcessingStatus,
    process_opportunity_candidate,
)
from app.intelligence.schemas import OpportunityAnalysis
from app.models import AIOpportunityAnalysis, Opportunity
from app.schemas import RawOpportunity


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


def _raw(**overrides: object) -> RawOpportunity:
    values: dict[str, object] = {
        "organisation_name": "Example Ministry",
        "title": "Independent assessment of project controls",
        "source_name": "Example Procurement Portal",
        "source_url": "https://example.test/notices/123",
        "description": (
            "The ministry invites qualified firms to assess project financial "
            "management and internal controls. Proposals close 30 September 2026."
        ),
        "deadline": None,
        "country_context": "Rwanda",
    }
    values.update(overrides)
    return RawOpportunity(**values)


def _analysis(**overrides: object) -> OpportunityAnalysis:
    values: dict[str, object] = {
        "decision": "ACCEPT",
        "confidence": 92,
        "reason": "A specific and actionable professional-services notice.",
        "title": "Independent assessment of project controls",
        "buyer_name": "Example Ministry",
        "issuing_organisation": "Example Ministry",
        "country": "Rwanda",
        "location": "Kigali",
        "opportunity_type": "PROFESSIONAL_SERVICE",
        "status": "ACTIVE",
        "publication_date": "2026-09-01",
        "deadline": "2026-09-30",
        "contract_duration": "Three months",
        "estimated_value": "100000",
        "currency": "USD",
        "summary": "The buyer seeks an independent project controls assessment.",
        "scope": "Assess financial management and internal controls.",
        "services_required": ["Project controls assessment"],
        "eligibility_requirements": ["Registered consulting firm"],
        "experience_requirements": ["Comparable donor-funded assignments"],
        "qualification_requirements": ["Qualified multidisciplinary team"],
        "required_documents": ["Technical proposal"],
        "deliverables": ["Assessment report"],
        "submission_method": "Email",
        "submission_address": "procurement@example.test",
        "contact_information": ["procurement@example.test"],
        "original_source_url": "https://example.test/notices/123",
        "source_confidence": 90,
        "evidence": ["Proposals close 30 September 2026."],
    }
    values.update(overrides)
    return OpportunityAnalysis.model_validate(values)


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    monkeypatch.setattr(
        opportunity_pipeline,
        "SessionLocal",
        isolated_session_factory,
    )


@pytest.mark.parametrize(
    ("case", "raw", "analysis", "expected"),
    [
        (
            "genuine procurement notice",
            _raw(title="Supply of laboratory equipment"),
            _analysis(opportunity_type="PROCUREMENT"),
            OpportunityProcessingStatus.VERIFIED,
        ),
        (
            "genuine consultancy",
            _raw(title="Consultancy for a climate risk study"),
            _analysis(opportunity_type="CONSULTANCY"),
            OpportunityProcessingStatus.VERIFIED,
        ),
        (
            "professional service without keyword overlap",
            _raw(title="Independent assessment of project controls"),
            _analysis(opportunity_type="PROFESSIONAL_SERVICE"),
            OpportunityProcessingStatus.VERIFIED,
        ),
        (
            "job vacancy containing audit",
            _raw(title="Audit Manager Vacancy"),
            _analysis(
                decision="REJECT",
                opportunity_type="JOB_VACANCY",
                status="UNKNOWN",
            ),
            OpportunityProcessingStatus.REJECTED,
        ),
        (
            "training page containing consultancy",
            _raw(title="Consultancy skills training course"),
            _analysis(
                decision="REJECT",
                opportunity_type="TRAINING",
                status="UNKNOWN",
            ),
            OpportunityProcessingStatus.REJECTED,
        ),
        (
            "news article containing tender",
            _raw(title="News: tender process reforms announced"),
            _analysis(
                decision="REJECT",
                opportunity_type="NEWS",
                status="UNKNOWN",
            ),
            OpportunityProcessingStatus.REJECTED,
        ),
        (
            "generic tender directory",
            _raw(title="Browse thousands of tenders"),
            _analysis(
                decision="REJECT",
                opportunity_type="DIRECTORY",
                status="UNKNOWN",
            ),
            OpportunityProcessingStatus.REJECTED,
        ),
        (
            "paid tender aggregator",
            _raw(title="Subscribe to unlock tender documents"),
            _analysis(
                decision="REJECT",
                reason="Payment gates access to the actual notice.",
                opportunity_type="DIRECTORY",
                status="UNKNOWN",
            ),
            OpportunityProcessingStatus.REJECTED,
        ),
        (
            "closed opportunity",
            _raw(title="Closed request for proposals"),
            _analysis(
                decision="REJECT",
                opportunity_type="CLOSED_OPPORTUNITY",
                status="CLOSED",
            ),
            OpportunityProcessingStatus.REJECTED,
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_semantic_classification_quality_gate(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
    case: str,
    raw: RawOpportunity,
    analysis: OpportunityAnalysis,
    expected: OpportunityProcessingStatus,
) -> None:
    del case
    _configure(monkeypatch, isolated_session_factory)
    analyzer = RecordingAnalyzer([analysis])

    outcome = process_opportunity_candidate(raw, analyzer=analyzer)

    assert outcome.status is expected
    assert len(analyzer.calls) == 1
    if expected is OpportunityProcessingStatus.VERIFIED:
        assert outcome.filtered_opportunity is not None
    else:
        assert outcome.filtered_opportunity is None


def test_missing_deadline_does_not_block_specific_opportunity(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    analysis = _analysis(deadline=None, status="UNKNOWN")

    outcome = process_opportunity_candidate(
        _raw(deadline=None),
        analyzer=RecordingAnalyzer([analysis]),
    )

    assert outcome.status is OpportunityProcessingStatus.VERIFIED
    assert outcome.analysis is not None
    assert outcome.analysis.deadline is None


def test_missing_budget_remains_null(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    outcome = process_opportunity_candidate(
        _raw(),
        analyzer=RecordingAnalyzer([_analysis(estimated_value=None, currency=None)]),
    )

    assert outcome.status is OpportunityProcessingStatus.VERIFIED
    assert outcome.analysis is not None
    assert outcome.analysis.estimated_value is None
    assert outcome.analysis.currency is None


def test_legitimate_third_party_repost_can_be_verified(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    raw = _raw(
        source_name="Regional Opportunity Bulletin",
        source_url="https://bulletin.example.test/reposts/123",
        outgoing_links=["https://ministry.example.test/", "https://ministry.example.test/rfp/123"],
    )
    analysis = _analysis(
        opportunity_type="RFP",
        original_source_url="https://ministry.example.test/rfp/123",
        buyer_website="https://ministry.example.test/",
        source_role="REPOST",
        evidence=["Buyer identified as Example Ministry."],
    )

    outcome = process_opportunity_candidate(
        raw,
        analyzer=RecordingAnalyzer([analysis]),
    )

    assert outcome.status is OpportunityProcessingStatus.VERIFIED
    assert outcome.analysis is not None
    assert outcome.analysis.original_source_url == (
        "https://ministry.example.test/rfp/123"
    )


def test_malformed_ai_response_is_an_error_and_not_cached(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    analyzer = RecordingAnalyzer([{"decision": "ACCEPT"}])

    outcome = process_opportunity_candidate(_raw(), analyzer=analyzer)

    assert outcome.status is OpportunityProcessingStatus.ERROR
    with isolated_session_factory() as db:
        record = db.scalar(select(AIOpportunityAnalysis))
        assert record is not None
        assert record.analysis_data is None
        assert record.error_reason == "Gemini returned an invalid opportunity analysis."


def test_gemini_api_failure_is_an_error_and_not_cached(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    analyzer = RecordingAnalyzer([GeminiAPIError("provider details")])

    outcome = process_opportunity_candidate(_raw(), analyzer=analyzer)

    assert outcome.status is OpportunityProcessingStatus.ERROR
    with isolated_session_factory() as db:
        record = db.scalar(select(AIOpportunityAnalysis))
        assert record is not None
        assert record.analysis_data is None
        assert record.error_reason == "Gemini opportunity analysis failed."


def test_identical_content_is_not_sent_to_gemini_twice(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    analyzer = RecordingAnalyzer([_analysis()])
    first = _raw()
    repost = replace(
        first,
        source_url="https://repost.example.test/notices/123",
    )

    first_outcome = process_opportunity_candidate(first, analyzer=analyzer)
    repost_outcome = process_opportunity_candidate(repost, analyzer=analyzer)

    assert first_outcome.analyzed is True
    assert repost_outcome.analyzed is False
    assert len(analyzer.calls) == 1
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AIOpportunityAnalysis)) == 2


def test_content_change_triggers_reanalysis(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    analyzer = RecordingAnalyzer(
        [
            _analysis(),
            _analysis(
                decision="REJECT",
                opportunity_type="AWARD_NOTICE",
                status="CLOSED",
                reason="The page now announces an award.",
            ),
        ]
    )
    original = _raw()
    changed = replace(
        original,
        description="Contract awarded to Example Consulting on 1 October 2026.",
    )

    first = process_opportunity_candidate(original, analyzer=analyzer)
    second = process_opportunity_candidate(changed, analyzer=analyzer)

    assert first.status is OpportunityProcessingStatus.VERIFIED
    assert second.status is OpportunityProcessingStatus.REJECTED
    assert second.analyzed is True
    assert len(analyzer.calls) == 2
    with isolated_session_factory() as db:
        record = db.scalar(select(AIOpportunityAnalysis))
        assert record is not None
        assert record.decision == "REJECT"
        assert record.error_reason is None


def test_country_context_change_triggers_reanalysis(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    analyzer = RecordingAnalyzer(
        [
            _analysis(country="Rwanda"),
            _analysis(
                decision="REVIEW",
                country="Kenya",
                status="UNKNOWN",
                reason="Relevance to Kenya is unclear.",
            ),
        ]
    )
    raw = _raw(country_context=None)

    first = process_opportunity_candidate(
        raw,
        country_context="Rwanda",
        analyzer=analyzer,
    )
    second = process_opportunity_candidate(
        raw,
        country_context="Kenya",
        analyzer=analyzer,
    )

    assert first.status is OpportunityProcessingStatus.VERIFIED
    assert second.status is OpportunityProcessingStatus.REVIEW
    assert len(analyzer.calls) == 2
    assert analyzer.calls[1]["country_hint"] == "Kenya"


@pytest.mark.parametrize("repost", [False, True], ids=["same-url", "identical-content-url"])
def test_pre_9d_cache_cannot_bypass_new_analysis(
    monkeypatch: pytest.MonkeyPatch, isolated_session_factory, repost: bool
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    raw = _raw()
    process_opportunity_candidate(raw, analyzer=RecordingAnalyzer([_analysis()]))
    # Reproduce the historical, unversioned fingerprint, independently of the
    # current builder, and store an actual pre-9D JSON shape.
    legacy_payload = "\n".join(
        " ".join(str(value or "").split()).strip()
        for value in (
            raw.title, raw.organisation_name, raw.source_name, raw.description,
            raw.deadline.isoformat() if raw.deadline else "", raw.country_context,
        )
    ).casefold()
    legacy_fingerprint = sha256(legacy_payload.encode("utf-8")).hexdigest()
    assert opportunity_pipeline.build_opportunity_content_fingerprint(raw) != legacy_fingerprint
    with isolated_session_factory() as db:
        opportunity = Opportunity(
            title=raw.title, category="PROFESSIONAL_SERVICE",
            source_name=raw.source_name, source_url=raw.source_url,
        )
        db.add(opportunity)
        db.flush()
        opportunity_id = opportunity.id
        record = db.scalar(select(AIOpportunityAnalysis))
        assert record is not None
        record.opportunity_id = opportunity_id
        record.content_fingerprint = legacy_fingerprint
        old_data = dict(record.analysis_data)
        for field in (
            "buyer_country", "delivery_countries", "eligibility_countries",
            "geographic_scope", "discovered_from_url", "source_role", "buyer_website",
        ):
            old_data.pop(field, None)
        old_data["country"] = "Rwanda"
        record.analysis_data = old_data
        db.commit()

    candidate = replace(raw, source_url="https://repost.example.test/notices/123") if repost else raw
    analyzer = RecordingAnalyzer([
        _analysis(buyer_country="Rwanda", delivery_countries=["Ghana", "Mozambique"])
    ])
    fresh = process_opportunity_candidate(candidate, analyzer=analyzer)
    cached = process_opportunity_candidate(candidate, analyzer=analyzer)
    assert fresh.analyzed is True
    assert cached.analyzed is False
    assert len(analyzer.calls) == 1
    assert fresh.analysis is not None
    assert fresh.analysis.delivery_countries == ["Ghana", "Mozambique"]
    assert fresh.analysis.country == "MULTI_COUNTRY"
    assert cached.analysis == fresh.analysis
    if not repost:
        assert fresh.duplicate_opportunity_id == opportunity_id
    with isolated_session_factory() as db:
        assert db.get(Opportunity, opportunity_id) is not None
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 1
        updated = db.scalar(select(AIOpportunityAnalysis).where(
            AIOpportunityAnalysis.source_url == candidate.source_url
        ))
        assert updated is not None
        assert updated.content_fingerprint == opportunity_pipeline.build_opportunity_content_fingerprint(candidate)
        assert updated.analysis_data["delivery_countries"] == ["Ghana", "Mozambique"]
        if repost:
            original = db.scalar(select(AIOpportunityAnalysis).where(
                AIOpportunityAnalysis.source_url == raw.source_url
            ))
            assert original.analysis_data == old_data
            assert original.content_fingerprint == legacy_fingerprint


def test_analyzer_version_bump_reanalyzes_once_then_reuses_cache(
    monkeypatch: pytest.MonkeyPatch, isolated_session_factory
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    raw = _raw()
    analyzer = RecordingAnalyzer([_analysis(), _analysis()])
    original_fingerprint = opportunity_pipeline.build_opportunity_content_fingerprint(raw)
    assert process_opportunity_candidate(raw, analyzer=analyzer).analyzed is True
    assert process_opportunity_candidate(raw, analyzer=analyzer).analyzed is False
    monkeypatch.setattr(opportunity_pipeline, "OPPORTUNITY_ANALYSIS_VERSION", "test-next-version")
    assert opportunity_pipeline.build_opportunity_content_fingerprint(raw) != original_fingerprint
    assert process_opportunity_candidate(raw, analyzer=analyzer).analyzed is True
    assert process_opportunity_candidate(raw, analyzer=analyzer).analyzed is False
    assert len(analyzer.calls) == 2


def test_opportunity_version_does_not_invalidate_other_stage_fingerprints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.intelligence.matching_engine import build_match_fingerprint
    from app.source_discovery import build_source_fingerprint

    analysis = _analysis(delivery_countries=["Ghana"])
    source_inputs = dict(url="https://example.test/", meaningful_content="Notice", country="Rwanda")
    match_inputs = dict(company_profile={"countries": ["Ghana"]}, opportunity=analysis, minimum_score=70)
    source_before = build_source_fingerprint(**source_inputs)
    match_before = build_match_fingerprint(**match_inputs)
    identity_before = opportunity_pipeline.build_semantic_opportunity_fingerprint(analysis)
    monkeypatch.setattr(opportunity_pipeline, "OPPORTUNITY_ANALYSIS_VERSION", "test-next-version")
    assert build_source_fingerprint(**source_inputs) == source_before
    assert build_match_fingerprint(**match_inputs) == match_before
    assert opportunity_pipeline.build_semantic_opportunity_fingerprint(analysis) == identity_before
