from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import select, event

from app.intelligence import matching_engine, triage_engine
from app.intelligence.applicant_evidence import direct_applicant_types
from app.intelligence.gap_evidence import normalize_company_gap, normalize_match_gaps
from app.intelligence.match_store import match_is_current
from app.intelligence.schemas import BidderEligibility
from app.intelligence.gemini_client import GeminiClient
from app.models import (
    AIOpportunityAnalysis,
    CompanyOpportunityMatch,
    CompanyOpportunityTriage,
    Organization,
)

from .test_matching_engine import _analysis, _configure, _match, _seed
from .test_triage import result


@pytest.mark.parametrize(
    "quote,types",
    [
        (
            "AWF Rwanda invites qualified individual consultants to submit competitive proposals to undertake the project endline evaluation.",
            ["INDIVIDUAL"],
        ),
        (
            "An Agency invites qualified individual consultants to submit proposals to conduct the final evaluation.",
            ["INDIVIDUAL"],
        ),
        (
            "Qualified consulting firms are invited to submit proposals for this assignment.",
            ["CONSULTING_FIRM"],
        ),
        ("Only registered NGOs may apply for this grant.", ["NGO"]),
        (
            "Firms and joint ventures may submit expressions of interest for the consultancy.",
            ["CONSULTING_FIRM", "JOINT_VENTURE"],
        ),
        (
            "Qualified consulting firms are invited to submit proposals, by 30 November 2026.",
            ["CONSULTING_FIRM"],
        ),
        (
            "The assignment concerns a project review; individual consultants may apply in Kigali.",
            ["INDIVIDUAL"],
        ),
        ("Individual consultants may apply (for the evaluation).", ["INDIVIDUAL"]),
        (
            "Individual consultants may apply for the evaluation, which concerns the final project phase.",
            ["INDIVIDUAL"],
        ),
    ],
)
def test_direct_clauses_with_context_keep_original_evidence(quote, types):
    b = BidderEligibility(
        status="RESTRICTED", allowed_applicant_types=types, evidence=[quote]
    )
    assert b.status.value == "RESTRICTED"
    assert {t.value for t in b.allowed_applicant_types} == set(types)
    assert b.evidence_strength.value == "DIRECT"
    assert b.evidence == [quote]


@pytest.mark.parametrize(
    "quote",
    [
        "Notification of Selected individual",
        "CV(s) of proposed consultant(s)",
        "The consultant shall perform the evaluation for the grant.",
        "Individual consultants may apply only if registered as firms.",
        "Individual consultants may apply, or firms may submit.",
        "Individual consultants may apply except those without a license.",
        "The selected consultant is invited to conduct an evaluation.",
    ],
)
def test_personnel_and_unresolved_qualifiers_do_not_create_individual_only(quote):
    b = BidderEligibility(
        status="RESTRICTED", allowed_applicant_types=["INDIVIDUAL"], evidence=[quote]
    )
    assert b.status.value == "UNSPECIFIED"


def test_separate_direct_clauses_cannot_narrow_broader_permission():
    quote = "Individual consultants may apply for this assignment. Firms may submit proposals by Friday."
    assert direct_applicant_types([quote]) == {"INDIVIDUAL", "CONSULTING_FIRM"}


def test_purpose_mentions_of_other_actors_do_not_change_applicant_clause():
    assert direct_applicant_types([
        'Consulting firms may submit proposals to conduct training for individual consultants.'
    ]) == {'CONSULTING_FIRM'}


def test_normalized_gap_persists_and_caches_without_changing_match_decision(monkeypatch, isolated_session_factory):
    _configure(monkeypatch, isolated_session_factory)
    a = _analysis()
    oid, companies = _seed(isolated_session_factory, analysis=a)
    raw = _match(companies[0], score=75, strength='STRONG', gaps=['Absence of specialized expertise in digital interoperability.'])
    generator = Mock(return_value=raw)
    first = matching_engine.match_verified_opportunity(oid, matcher=generator)[0]
    second = matching_engine.match_verified_opportunity(oid, matcher=generator)[0]
    assert second.status.value == 'CACHED'
    assert first.match.score == raw.score and first.qualifies_for_feed
    assert 'not documented' in first.match.potential_gaps[0]
    with isolated_session_factory() as db:
        assert db.scalar(select(CompanyOpportunityMatch)).potential_gaps == first.match.potential_gaps
        assert db.scalar(select(AIOpportunityAnalysis)).analysis_data == a.model_dump(mode='json')
    generator.assert_called_once()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("No experience in Ghana.", "Experience in Ghana is not documented"),
        (
            "Experience is limited to Rwanda.",
            "Delivery experience outside Rwanda is not documented",
        ),
        (
            "delivery footprint is concentrated in Rwanda",
            "Delivery experience outside Rwanda is not documented",
        ),
        (
            "Absence of specialized expertise in digital interoperability.",
            "Specialized expertise in digital interoperability is not documented",
        ),
        ("The firm lacks overseas presence.", "Overseas presence is not documented"),
    ],
)
def test_missing_company_evidence_not_absence(text, expected):
    profile = {
        "geography": {"base_country": "Rwanda", "demonstrated_delivery_countries": None}
    }
    output = normalize_company_gap(text, profile)
    assert expected in output
    assert normalize_company_gap(output, profile) == output


@pytest.mark.parametrize(
    "text,profile",
    [
        (
            "Company does not provide construction supervision.",
            {"exclusions": {"services": ["construction supervision"]}},
        ),
        (
            "The company does not operate in Ghana.",
            {"exclusions": {"countries": ["Ghana"]}},
        ),
        (
            "The firm lacks a required certification.",
            {"description": "The firm lacks a required certification."},
        ),
        (
            "No experience in Ghana.",
            {
                "experience": {
                    "track_record": ["The company has no experience in Ghana."]
                }
            },
        ),
    ],
)
def test_explicit_negative_facts_are_preserved(text, profile):
    assert normalize_company_gap(text, profile) == text


def test_field_scope_score_strength_and_eligibility_untouched():
    match = _match(
        1,
        score=75,
        strength="STRONG",
        explanation="The firm lacks overseas presence.",
        gaps=["Absence of specialized expertise in digital interoperability."],
        concerns=["No applicants without certification may bid."],
    )
    before = match.model_dump()
    normalized = normalize_match_gaps(match, {})
    for key in before.keys() - {"explanation", "potential_gaps"}:
        assert normalized.model_dump()[key] == before[key]
    assert match.model_dump() == before
    for text in [
        "The opportunity requires overseas experience.",
        '"No experience in Ghana."',
        "Firms must provide a certificate.",
        "No documented experience in Ghana.",
    ]:
        assert normalize_company_gap(text, {}) == text


def test_positive_documented_evidence_is_not_erased():
    profile = {
        "geography": {"demonstrated_delivery_countries": ["Ghana"]},
        "capabilities": ["digital interoperability"],
    }
    for text in (
        "No experience in Ghana.",
        "Absence of specialized expertise in digital interoperability.",
    ):
        normalized = normalize_company_gap(text, profile)
        assert "Review supplied evidence" in normalized
        assert "not documented" not in normalized

    assert "not documented" not in normalize_company_gap(
        "Experience is limited to Rwanda.", profile
    )
    statement = "No experience in Ghana is required."
    assert normalize_company_gap(statement, {}) == statement


def test_excluding_market_is_not_proof_of_no_past_experience():
    profile = {
        "exclusions": {"countries": ["Ghana"]},
        "description": "Operations are limited to Rwanda.",
    }
    assert "not documented" in normalize_company_gap("No experience in Ghana.", profile)
    assert "not documented" in normalize_company_gap(
        "Experience is limited to Rwanda.", profile
    )


@pytest.fixture
def two_current(monkeypatch, isolated_session_factory):
    _configure(monkeypatch, isolated_session_factory)
    a = _analysis(
        deadline=datetime.now(timezone.utc).date() + timedelta(days=20),
        experience_requirements=[],
        eligibility_requirements=[],
    )
    oid, companies = _seed(
        isolated_session_factory,
        analysis=a,
        companies=[{"name": "Company A"}, {"name": "Company B"}],
    )
    matcher = Mock(
        side_effect=lambda **kw: _match(
            kw["company_id"],
            score=95 if kw["company_id"] == companies[0] else 80,
            strength="STRONG",
        )
    )
    matching_engine.match_verified_opportunity(oid, matcher=matcher)
    forbidden = Mock(side_effect=AssertionError("AI on rendering"))
    monkeypatch.setattr(GeminiClient, "generate_structured", forbidden)
    monkeypatch.setattr(GeminiClient, "generate_text", forbidden)
    return oid, companies, a, forbidden


def test_profile_change_only_stales_own_match_and_all_company_selects_current(
    two_current, client, isolated_session_factory
):
    oid, companies, a, blocked = two_current
    assert client.get("/").context["opportunities"][0].display_match_score == 95
    with isolated_session_factory() as db:
        db.get(Organization, companies[0]).description = "Changed profile"
        db.commit()
    assert not client.get("/", params={"company_id": companies[0]}).context[
        "opportunities"
    ]
    remaining = client.get("/", params={"company_id": companies[1]}).context[
        "opportunities"
    ]
    assert len(remaining) == 1 and remaining[0].display_match_score == 80
    rows = client.get("/").context["opportunities"]
    assert len(rows) == 1 and rows[0].display_match_company_id == companies[1]
    detail = client.get(f"/opportunities/{oid}", params={"company_id": companies[0]})
    assert detail.status_code == 200 and detail.context["stored_match"] is None
    assert "Match assessment needs refresh" in detail.text
    assert detail.context["opportunity"].title == a.title
    best = client.get(f"/opportunities/{oid}")
    assert best.context["stored_match"].organization_id == companies[1]
    with isolated_session_factory() as db:
        old = db.scalar(
            select(CompanyOpportunityMatch).where(
                CompanyOpportunityMatch.organization_id == companies[0]
            )
        )
        assert old.score == 95 and old.qualifies_for_feed
    blocked.assert_not_called()


def test_eligibility_input_change_stales_all_matches(
    two_current, client, isolated_session_factory
):
    oid, companies, _, blocked = two_current
    with isolated_session_factory() as db:
        row = db.scalar(select(AIOpportunityAnalysis))
        data = dict(row.analysis_data)
        data["bidder_eligibility"] = {
            "status": "RESTRICTED",
            "allowed_applicant_types": ["INDIVIDUAL"],
            "evidence": ["Individuals may apply for the evaluation."],
        }
        row.analysis_data = data
        db.commit()
    assert not client.get("/").context["opportunities"]
    for cid in companies:
        assert (
            client.get(f"/opportunities/{oid}", params={"company_id": cid}).context[
                "stored_match"
            ]
            is None
        )
    blocked.assert_not_called()


def test_currentness_is_fingerprint_not_timestamp(
    two_current, isolated_session_factory
):
    _, companies, a, _ = two_current
    profile = matching_engine.load_active_company_profiles([companies[0]])[0]
    with isolated_session_factory() as db:
        match = db.scalar(select(CompanyOpportunityMatch))
        match.matched_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        assert match_is_current(match, profile=profile, analysis=a)
        match.match_fingerprint = "stale"
        match.matched_at = datetime.now(timezone.utc)
        assert not match_is_current(match, profile=profile, analysis=a)


def test_triage_fingerprint_currentness_and_history_no_render_writes(
    two_current, client, isolated_session_factory
):
    oid, companies, _, blocked = two_current
    results = triage_engine.triage_verified_opportunity(
        oid, analyzer=Mock(return_value=result(experience_evidence="UNKNOWN"))
    )
    assert all(t.status == "TRIAGED" for t in results)
    response = client.get(f"/opportunities/{oid}", params={"company_id": companies[0]})
    assert response.context["opportunity"].display_triage is not None
    with isolated_session_factory() as db:
        row = db.scalar(
            select(CompanyOpportunityTriage).where(
                CompanyOpportunityTriage.organization_id == companies[0]
            )
        )
        row.triage_fingerprint = "old-contract"
        db.commit()
    writes = []

    def audit(conn, cursor, statement, params, context, many):
        if statement.lstrip().split()[0].upper() in {
            "INSERT",
            "UPDATE",
            "DELETE",
            "CREATE",
            "ALTER",
        }:
            writes.append(statement)

    engine = isolated_session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", audit)
    try:
        detail = client.get(
            f"/opportunities/{oid}", params={"company_id": companies[0]}
        )
        assert detail.context["stored_match"] is not None
        assert detail.context["opportunity"].display_triage is None
        client.get("/")
    finally:
        event.remove(engine, "before_cursor_execute", audit)
    assert not writes
    with isolated_session_factory() as db:
        assert (
            db.scalar(
                select(CompanyOpportunityTriage).where(
                    CompanyOpportunityTriage.organization_id == companies[0]
                )
            ).triage_fingerprint
            == "old-contract"
        )
    blocked.assert_not_called()


def test_full_tui_purpose_clause_blocks_two_corporate_matches(
    monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    a = _analysis(
        bidder_eligibility={
            "status": "RESTRICTED",
            "allowed_applicant_types": ["INDIVIDUAL"],
            "evidence": [
                "AWF Rwanda invites qualified individual consultants to submit competitive proposals to undertake the project endline evaluation."
            ],
        }
    )
    oid, companies = _seed(
        isolated_session_factory,
        analysis=a,
        companies=[{"name": "UT CPA LTD"}, {"name": "UT Consulting"}],
    )
    matcher = Mock(
        side_effect=lambda **kw: _match(
            kw["company_id"],
            score=0,
            strength="NOT_A_MATCH",
            concerns=["Individual applicants only"],
        )
    )
    outcomes = matching_engine.match_verified_opportunity(oid, matcher=matcher)
    assert all(
        o.match.applicant_eligibility.value == "CONFLICT" and not o.qualifies_for_feed
        for o in outcomes
    )
    forbidden = Mock(side_effect=AssertionError("No triage for conflict"))
    assert all(
        t.status == "SKIPPED"
        for t in triage_engine.triage_verified_opportunity(
            oid, matches=outcomes, analyzer=forbidden
        )
    )
    forbidden.assert_not_called()
