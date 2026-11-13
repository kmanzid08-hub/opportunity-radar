from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select

from app.intelligence import matching_engine, triage_engine
from app.intelligence.gemini_client import GeminiAPIError, GeminiClient
from app.intelligence.triage_analyzer import analyze_triage
from app.intelligence.triage_engine import triage_verified_opportunity, validate_triage
from app.intelligence.triage_schemas import TriageResult
from app.models import (
    AIOpportunityAnalysis,
    CompanyOpportunityMatch,
    CompanyOpportunityTriage,
    Opportunity,
    OpportunityPreference,
)

from .test_matching_engine import _analysis, _configure, _match, _seed


TODAY = datetime.now(timezone.utc).date()


def result(**overrides):
    values = {
        "priority": "HIGH",
        "priority_score": 85,
        "bid_readiness": "READY",
        "urgency": "HIGH",
        "estimated_effort": "LOW",
        "key_reasons": [
            "CONFIRMED: strong service fit; documented eligibility; deadline approaching."
        ],
        "critical_gaps": [],
        "eligibility_risks": [],
        "recommended_next_action": "Begin technical proposal preparation.",
        "budget_evidence": "UNKNOWN",
        "deadline_evidence": "CONFIRMED",
        "eligibility_evidence": "CONFIRMED",
        "experience_evidence": "POSSIBLE",
        "exclusion_applies": False,
        "required_documents": [],
    }
    values.update(overrides)
    return TriageResult.model_validate(values)


@pytest.fixture
def setup_triage(monkeypatch, isolated_session_factory):
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(
        isolated_session_factory,
        analysis=_analysis(
            deadline=TODAY + timedelta(days=3),
            bidder_eligibility={"status": "UNRESTRICTED", "evidence": ["All applicant types are permitted."]},
        ),
    )
    matcher = Mock(
        return_value=_match(company_ids[0], score=95, strength="VERY_STRONG")
    )
    # READY fixture must document the experience requirement, not merely POSSIBLE.
    with isolated_session_factory() as db:
        preference = db.scalar(select(OpportunityPreference))
        preference.intelligence = {"relevant_experience": ["Ten years and three similar control-review assignments"]}
        db.commit()
    analyzer = Mock(return_value=result(experience_evidence="CONFIRMED"))
    monkeypatch.setattr(matching_engine, "match_company_to_opportunity", matcher)
    monkeypatch.setattr(triage_engine, "analyze_triage", analyzer)
    return opportunity_id, company_ids[0], matcher, analyzer


def test_strong_eligible_approaching_opportunity_is_triaged_and_cached(
    setup_triage,
    isolated_session_factory,
):
    opportunity_id, company_id, matcher, analyzer = setup_triage
    first = triage_verified_opportunity(opportunity_id)[0]
    second = triage_verified_opportunity(opportunity_id)[0]
    assert first.status == "TRIAGED"
    assert first.result.priority.value == "HIGH"
    assert first.result.bid_readiness.value == "READY"
    assert second.status == "CACHED"
    matcher.assert_called_once()
    analyzer.assert_called_once()
    with isolated_session_factory() as db:
        record = db.scalar(select(CompanyOpportunityTriage))
        assert record.organization_id == company_id
        assert record.result_data["priority_score"] == 85
        assert (
            record.match_fingerprint
            == db.scalar(select(CompanyOpportunityMatch)).match_fingerprint
        )
        assert (
            db.scalar(select(func.count()).select_from(CompanyOpportunityTriage)) == 1
        )


@pytest.mark.parametrize("effort", ["LOW", "HIGH", "UNKNOWN"])
def test_preparation_effort_is_retained(setup_triage, effort):
    opportunity_id, _, _, analyzer = setup_triage
    analyzer.return_value = result(estimated_effort=effort)
    assert (
        triage_verified_opportunity(opportunity_id)[0].result.estimated_effort.value
        == effort
    )


@pytest.mark.parametrize("score", [0, 15, 20, 69])
def test_nonqualifying_match_skips_triage(
    setup_triage, isolated_session_factory, score
):
    opportunity_id, company_id, matcher, analyzer = setup_triage
    matcher.return_value = _match(company_id, score=score, strength="WEAK")
    analyzer.return_value = result(
        priority="LOW", priority_score=15, urgency="CRITICAL"
    )
    with isolated_session_factory() as db:
        db.get(Opportunity, opportunity_id).deadline = TODAY + timedelta(days=1)
        db.commit()
    outcome = triage_verified_opportunity(opportunity_id)[0]
    assert outcome.status == "SKIPPED"
    assert outcome.result is None
    assert outcome.error_category is None
    analyzer.assert_not_called()
    with isolated_session_factory() as db:
        assert db.scalar(select(CompanyOpportunityMatch)).qualifies_for_feed is False
        assert db.scalar(select(CompanyOpportunityMatch)).score == score
        assert db.scalar(select(CompanyOpportunityTriage)) is None


@pytest.mark.parametrize(
    "invalid",
    [
        {"priority": "HIGH"},
        {"priority": "URGENT"},
    ],
)
def test_deadline_cannot_promote_weak_match(setup_triage, invalid):
    opportunity_id, company_id, matcher, analyzer = setup_triage
    matcher.return_value = _match(company_id, score=20, strength="WEAK")
    analyzer.return_value = result(**invalid)
    assert triage_verified_opportunity(opportunity_id)[0].status == "SKIPPED"
    analyzer.assert_not_called()


def test_strong_match_major_eligibility_gap_is_not_ready(setup_triage):
    opportunity_id, company_id, matcher, analyzer = setup_triage
    matcher.return_value = _match(
        company_id,
        score=95,
        strength="VERY_STRONG",
        concerns=["Required registration is absent"],
    )
    analyzer.return_value = result(
        priority="DO_NOT_PURSUE",
        priority_score=5,
        bid_readiness="LIKELY_INELIGIBLE",
        eligibility_evidence="CONFLICT",
        critical_gaps=["Required registration absent"],
        eligibility_risks=["CONFLICT: registration requirement not met"],
        recommended_next_action="Do not pursue unless registration can be satisfied.",
    )
    assert (
        triage_verified_opportunity(opportunity_id)[0].result.bid_readiness.value
        == "LIKELY_INELIGIBLE"
    )
    analyzer.return_value = result(experience_evidence="CONFIRMED")
    # Force a new fingerprint, exposing the unsafe READY result.
    assert (
        triage_verified_opportunity(
            opportunity_id, as_of_date=TODAY + timedelta(days=1)
        )[0].status
        == "ERROR"
    )


def test_unknown_deadline_budget_eligibility_experience_remain_unknown(
    monkeypatch,
    isolated_session_factory,
):
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(
        isolated_session_factory,
        analysis=_analysis(
            deadline=None,
            estimated_value=None,
            eligibility_requirements=[],
            experience_requirements=[],
        ),
    )
    analyzer = Mock(
        return_value=result(
            priority="MEDIUM",
            priority_score=60,
            bid_readiness="UNKNOWN",
            urgency="UNKNOWN",
            deadline_evidence="UNKNOWN",
            eligibility_evidence="UNKNOWN",
            experience_evidence="UNKNOWN",
        )
    )
    outcome = triage_verified_opportunity(
        opportunity_id,
        analyzer=analyzer,
        matcher=Mock(return_value=_match(company_ids[0], score=88, strength="STRONG")),
    )[0]
    assert outcome.status == "TRIAGED"
    assert outcome.result.budget_evidence.value == "UNKNOWN"
    assert analyzer.call_args.kwargs["effective_deadline"] is None


def test_excluded_opportunity_is_do_not_pursue(setup_triage):
    opportunity_id, _, _, analyzer = setup_triage
    analyzer.return_value = result(
        priority="DO_NOT_PURSUE",
        priority_score=0,
        exclusion_applies=True,
        key_reasons=["CONFLICT: assignment is explicitly excluded by the company."],
    )
    assert (
        triage_verified_opportunity(opportunity_id)[0].result.priority.value
        == "DO_NOT_PURSUE"
    )


@pytest.mark.parametrize(
    "changed", ["profile", "deadline", "analysis", "status", "model", "day"]
)
def test_input_changes_invalidate_only_relevant_caches(
    setup_triage,
    isolated_session_factory,
    monkeypatch,
    changed,
):
    opportunity_id, _, matcher, analyzer = setup_triage
    triage_verified_opportunity(opportunity_id)
    with isolated_session_factory() as db:
        if changed == "profile":
            db.scalar(select(OpportunityPreference)).intelligence = {
                "capabilities": ["New evidence"]
            }
        elif changed == "deadline":
            db.get(Opportunity, opportunity_id).deadline = TODAY + timedelta(days=2)
        elif changed == "analysis":
            record = db.scalar(select(AIOpportunityAnalysis))
            record.analysis_data = {
                **record.analysis_data,
                "scope": "Changed delivery scope",
            }
        elif changed == "status":
            db.get(Opportunity, opportunity_id).status = "Rejected"
            analyzer.return_value = result(priority="DO_NOT_PURSUE", priority_score=0)
        db.commit()
    if changed == "model":
        monkeypatch.setenv("GEMINI_MODEL", "mock-model-change")
    semantic_analyzer = Mock(
        side_effect=AssertionError("Do not reanalyze on triage refresh")
    )
    from app.intelligence import opportunity_pipeline

    monkeypatch.setattr(opportunity_pipeline, "analyze_opportunity", semantic_analyzer)
    second = triage_verified_opportunity(
        opportunity_id,
        as_of_date=TODAY + timedelta(days=1) if changed == "day" else TODAY,
    )[0]
    assert second.status == "TRIAGED"
    assert analyzer.call_count == 2
    assert matcher.call_count == (2 if changed in {"profile", "analysis"} else 1)
    semantic_analyzer.assert_not_called()
    if changed == "deadline":
        assert analyzer.call_args.kwargs["effective_deadline"] == TODAY + timedelta(
            days=2
        )


@pytest.mark.parametrize(
    "failure", [GeminiAPIError("sensitive provider text"), {"priority": "HIGH"}]
)
def test_failure_is_isolated_recorded_and_not_cached(
    setup_triage,
    isolated_session_factory,
    failure,
):
    opportunity_id, _, matcher, analyzer = setup_triage
    analyzer.side_effect = [failure, result()]
    first = triage_verified_opportunity(opportunity_id)[0]
    assert first.status == "ERROR"
    assert first.result is None
    with isolated_session_factory() as db:
        record = db.scalar(select(CompanyOpportunityTriage))
        assert record.result_data is None
        assert "sensitive" not in record.error_reason
        assert db.scalar(select(CompanyOpportunityMatch)).qualifies_for_feed
        assert db.get(Opportunity, opportunity_id) is not None
    assert triage_verified_opportunity(opportunity_id)[0].status == "TRIAGED"
    matcher.assert_called_once()


def test_company_specific_priorities_and_failure_isolation(
    monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(
        isolated_session_factory,
        analysis=_analysis(deadline=TODAY + timedelta(days=3)),
        companies=[
            {"name": "Assurance Company"},
            {"name": "Research Company"},
            {"name": "Third Company"},
        ],
    )
    matcher = Mock(
        side_effect=lambda **kw: _match(kw["company_id"], score=90, strength="STRONG")
    )
    analyzer = Mock(
        side_effect=[
            GeminiAPIError("No service"),
            result(priority="HIGH"),
            result(priority="LOW", priority_score=25),
        ]
    )
    outcomes = triage_verified_opportunity(
        opportunity_id, matcher=matcher, analyzer=analyzer
    )
    assert [item.company_id for item in outcomes] == company_ids
    assert [item.status for item in outcomes] == ["ERROR", "TRIAGED", "TRIAGED"]
    # Unverified bidder type caps otherwise strong technical fit to review/medium.
    assert [item.result.priority.value for item in outcomes[1:]] == ["MEDIUM", "LOW"]
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(CompanyOpportunityMatch)) == 3
        assert (
            db.scalar(select(func.count()).select_from(CompanyOpportunityTriage)) == 3
        )


def test_document_categories_are_exact_and_no_invented_documents():
    analysis = _analysis(
        required_documents=["Registration certificate", "Technical proposal"]
    )
    match = _match(1, score=92, strength="STRONG")
    valid = result(
        required_documents=[
            {"name": "Registration certificate", "category": "LEGAL_REGISTRATION"},
            {"name": "Technical proposal", "category": "TECHNICAL_PROPOSAL"},
        ]
    )
    kwargs = dict(
        analysis=analysis,
        match=match,
        minimum_score=70,
        deadline=analysis.deadline,
        status="ACTIVE",
    )
    assert validate_triage(valid, **kwargs) == valid
    for documents in ([], [{"name": "Invented paperwork", "category": "OTHER"}]):
        with pytest.raises(ValueError, match="Required documents"):
            validate_triage(result(required_documents=documents), **kwargs)


def test_gemini_receives_complete_inputs_and_evidence_rules():
    client = Mock(spec=GeminiClient)
    client.generate_structured.return_value = result()
    profile = {
        "name": "Advisory",
        "capabilities": ["Assurance"],
        "exclusions": {"services": ["Recruitment"]},
    }
    analysis = _analysis()
    match = _match(1, score=91, strength="STRONG", gaps=["Confirm staffing capacity"])
    returned = analyze_triage(
        company_profile=profile,
        opportunity=analysis,
        match=match,
        effective_deadline=None,
        effective_status="ACTIVE",
        as_of_date=TODAY,
        client=client,
    )
    payload = json.loads(client.generate_structured.call_args.args[0])
    assert payload["company_profile"] == profile
    assert payload["opportunity_analysis"] == analysis.model_dump(mode="json")
    assert payload["company_match"] == match.model_dump(mode="json")
    assert payload["effective_deadline"] is None
    assert payload["assessment_date"] == TODAY.isoformat()
    instruction = client.generate_structured.call_args.kwargs["system_instruction"]
    for label in (
        "CONFIRMED",
        "POSSIBLE",
        "UNKNOWN",
        "CONFLICT",
        "untrusted",
        "chain-of-thought",
    ):
        assert label in instruction
    assert returned == result()


def test_scanner_runs_triage_after_matching_with_no_duplicate_match_call(setup_triage):
    from app import scanner

    opportunity_id, _, matcher, analyzer = setup_triage
    profiles = matching_engine.load_active_company_profiles(None)
    assert scanner.match_saved_opportunity(opportunity_id, profiles) == (0, 1)
    assert scanner.match_saved_opportunity(opportunity_id, profiles) == (0, 1)
    matcher.assert_called_once()
    analyzer.assert_called_once()


def test_gate_uses_cached_authoritative_flag_not_recomputed_score(
    setup_triage,
    isolated_session_factory,
):
    opportunity_id, _, matcher, analyzer = setup_triage
    matching_engine.match_verified_opportunity(opportunity_id)
    with isolated_session_factory() as db:
        stored = db.scalar(select(CompanyOpportunityMatch))
        assert stored.score == 95
        stored.qualifies_for_feed = False
        db.commit()
    assert triage_verified_opportunity(opportunity_id)[0].status == "SKIPPED"
    analyzer.assert_not_called()
    with isolated_session_factory() as db:
        db.scalar(select(CompanyOpportunityMatch)).qualifies_for_feed = True
        db.commit()
    assert triage_verified_opportunity(opportunity_id)[0].status == "TRIAGED"
    analyzer.assert_called_once()
    matcher.assert_called_once()


@pytest.mark.parametrize(
    "score,strength",
    [(0, "NOT_A_MATCH"), (15, "NOT_A_MATCH"), (69, "WEAK"), (95, "VERY_STRONG")],
)
def test_scanner_never_runs_automatic_readiness(
    setup_triage, monkeypatch, score, strength
):
    from app import scanner
    from app.intelligence import readiness_engine

    opportunity_id, company_id, matcher, analyzer = setup_triage
    matcher.return_value = _match(company_id, score=score, strength=strength)
    forbidden = Mock(side_effect=AssertionError("Readiness must be explicit"))
    monkeypatch.setattr(readiness_engine, "analyze_opportunity_readiness", forbidden)
    monkeypatch.setattr(readiness_engine, "extract_requirements", forbidden)
    scanner.match_saved_opportunity(
        opportunity_id, matching_engine.load_active_company_profiles(None)
    )
    assert analyzer.call_count == int(score == 95)
    forbidden.assert_not_called()


def test_pages_use_stored_triage_without_ai(setup_triage, client, monkeypatch):
    opportunity_id, company_id, matcher, analyzer = setup_triage
    triage_verified_opportunity(opportunity_id)
    forbidden = Mock(side_effect=AssertionError("AI must not run during rendering"))
    monkeypatch.setattr(GeminiClient, "generate_structured", forbidden)
    monkeypatch.setattr(GeminiClient, "generate_text", forbidden)
    for url in (
        "/",
        f"/?company_id={company_id}",
        f"/opportunities/{opportunity_id}?company_id={company_id}",
        "/pipeline",
    ):
        response = client.get(url)
        assert response.status_code == 200
        if url != "/pipeline":
            assert "High" in response.text
            if "opportunities/" in url:
                assert "Begin technical proposal preparation." in response.text
    forbidden.assert_not_called()
    matcher.assert_called_once()
    analyzer.assert_called_once()


def test_stale_deadline_triage_is_not_shown_or_recalculated(
    setup_triage, client, isolated_session_factory
):
    opportunity_id, company_id, _, analyzer = setup_triage
    triage_verified_opportunity(opportunity_id)
    with isolated_session_factory() as db:
        db.get(Opportunity, opportunity_id).deadline = TODAY + timedelta(days=1)
        db.commit()
    response = client.get(f"/opportunities/{opportunity_id}?company_id={company_id}")
    assert 'id="triage-priority"' not in response.text
    analyzer.assert_called_once()


def test_feed_sort_is_priority_then_match_then_deadline_then_freshness():
    from app.main import opportunity_sort_key

    def opportunity(identifier, priority, score, days, discovered):
        item = Opportunity(
            id=identifier,
            deadline=TODAY + timedelta(days=days),
            first_discovered_at=discovered,
        )
        item.display_triage = result(priority=priority)
        item.display_match_score = score
        item.display_match_strength = "STRONG"
        return item

    now = datetime.now(timezone.utc)
    items = [
        opportunity(1, "LOW", 99, 1, now),
        opportunity(2, "HIGH", 85, 10, now),
        opportunity(3, "HIGH", 85, 2, now - timedelta(days=1)),
        opportunity(4, "URGENT", 80, 1, now),
        opportunity(5, "HIGH", 85, 2, now),
        opportunity(6, "HIGH", 85, 2, now),
    ]
    assert [item.id for item in sorted(items, key=opportunity_sort_key)] == [
        4,
        5,
        6,
        3,
        2,
        1,
    ]


def test_company_selection_displays_only_that_company_triage(
    monkeypatch,
    isolated_session_factory,
    client,
):
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(
        isolated_session_factory,
        analysis=_analysis(deadline=TODAY + timedelta(days=3)),
        companies=[{"name": "First Advisory"}, {"name": "Second Advisory"}],
    )
    first, second = company_ids
    # Equal match scores still select a deterministic company in the all view.
    matcher = Mock(
        side_effect=lambda **kw: _match(kw["company_id"], score=90, strength="STRONG")
    )
    analyzer = Mock(
        side_effect=[
            result(priority="HIGH", key_reasons=["First company's stored reasoning"]),
            result(
                priority="LOW",
                priority_score=20,
                key_reasons=["Second company's stored reasoning"],
            ),
        ]
    )
    triage_verified_opportunity(opportunity_id, matcher=matcher, analyzer=analyzer)
    all_view = client.get("/")
    assert "Priority: Medium" in all_view.text
    assert "Bid decision: Verify eligibility" in all_view.text
    assert "Priority: Low" not in all_view.text
    individual = client.get(f"/?company_id={second}")
    assert "Priority: Low" in individual.text
    assert "Priority: Medium" not in individual.text
    for company_id, expected, absent in [
        (first, "First company's stored reasoning", "Second company"),
        (second, "Second company's stored reasoning", "First company"),
    ]:
        response = client.get(
            f"/opportunities/{opportunity_id}?company_id={company_id}"
        )
        assert expected.replace("'", "&#39;") in response.text
        assert absent not in response.text


@pytest.mark.parametrize(
    "override",
    [
        {"deadline_evidence": "CONFIRMED"},
        {"budget_evidence": "CONFIRMED"},
        {"eligibility_evidence": "CONFIRMED"},
        {"experience_evidence": "CONFIRMED"},
    ],
)
def test_missing_facts_cannot_be_labelled_confirmed(override):
    unknown = dict(
        priority="MEDIUM",
        bid_readiness="UNKNOWN",
        urgency="UNKNOWN",
        deadline_evidence="UNKNOWN",
        budget_evidence="UNKNOWN",
        eligibility_evidence="UNKNOWN",
        experience_evidence="UNKNOWN",
    )
    unknown.update(override)
    with pytest.raises(ValueError, match="Missing"):
        validate_triage(
            result(**unknown),
            analysis=_analysis(
                deadline=None, eligibility_requirements=[], experience_requirements=[]
            ),
            match=_match(1, score=90, strength="STRONG"),
            minimum_score=70,
            deadline=None,
            status="ACTIVE",
        )
