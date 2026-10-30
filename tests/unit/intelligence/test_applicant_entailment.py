import json
import logging
from datetime import date
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.intelligence import matching_engine, opportunity_pipeline, triage_engine
from app.intelligence.applicant_evidence import capability_only
from app.intelligence.company_matcher import COMPANY_MATCH_SYSTEM_INSTRUCTION
from app.intelligence.eligibility import (
    apply_match_eligibility,
    applicant_eligibility_state,
)
from app.intelligence.gemini_client import GeminiClient, GeminiResponseError
from app.intelligence.opportunity_analyzer import analyze_opportunity
from app.intelligence.schemas import BidderEligibility, CompanyMatch
from app.intelligence.triage_analyzer import TRIAGE_VERSION
from app.models import AIOpportunityAnalysis, CompanyOpportunityMatch
from app.schemas import RawOpportunity

from .test_matching_engine import _analysis, _configure, _match, _seed
from .test_triage import result


DIRECT = [
    ("Only individual consultants may apply.", ["INDIVIDUAL"]),
    ("Only individual consultants are eligible to apply.", ["INDIVIDUAL"]),
    ("Qualified individual consultants are invited to submit.", ["INDIVIDUAL"]),
    (
        "AWF Rwanda invites qualified individual consultants to submit competitive proposals.",
        ["INDIVIDUAL"],
    ),
    ("Consulting firms are invited.", ["CONSULTING_FIRM"]),
    ("Eligible bidders must be registered consulting firms.", ["CONSULTING_FIRM"]),
    ("Applications are open only to firms.", ["CONSULTING_FIRM"]),
    ("Firms and JVs may submit.", ["CONSULTING_FIRM", "JOINT_VENTURE"]),
    ("Joint ventures are eligible.", ["JOINT_VENTURE"]),
    ("Only NGOs registered in Rwanda may apply.", ["NGO"]),
    ("Non-governmental organizations may apply.", ["NGO"]),
    (
        "Consulting firms and individual consultants may submit.",
        ["CONSULTING_FIRM", "INDIVIDUAL"],
    ),
]
AMBIGUOUS = [
    "CV of consultant",
    "CVs of proposed consultants",
    "CV(s) of proposed consultant(s)",
    "Notification of Selected individual",
    "selected individual",
    "key expert CVs",
    "Proposed experts must hold a Master's degree",
    "Consultant qualifications",
    "The consultant shall perform the evaluation",
    "Team leader and key personnel",
    "Recruitment of individual consultants to lead the proposed team",
    "Individual consultants are not eligible to apply",
    "Not only individual consultants may apply",
    "We do not invite individual consultants to submit proposals",
    "Individual consultants must provide CVs. Firms may apply",
]


def restriction(quotes, types=None, strength="DIRECT"):
    return {
        "status": "RESTRICTED",
        "allowed_applicant_types": types or ["INDIVIDUAL"],
        "evidence": quotes,
        "evidence_strength": strength,
    }


@pytest.mark.parametrize("quote,types", DIRECT)
def test_direct_clause_entails_exact_applicant_types(quote, types):
    bidder = BidderEligibility.model_validate(restriction([quote], types))
    assert bidder.status.value == "RESTRICTED"
    assert {t.value for t in bidder.allowed_applicant_types} == set(types)
    assert bidder.evidence_strength.value == "DIRECT"
    assert bidder.evidence == [quote]


@pytest.mark.parametrize("quote", AMBIGUOUS)
def test_ambiguous_personnel_never_creates_individual_restriction(quote):
    bidder = BidderEligibility.model_validate(restriction([quote]))
    assert bidder.status.value == "UNSPECIFIED"
    assert bidder.allowed_applicant_types == [] and bidder.evidence == []
    assert bidder.evidence_strength.value == "AMBIGUOUS"


def test_broader_direct_permission_cannot_be_narrowed_to_individual():
    bidder = BidderEligibility.model_validate(
        restriction(["Consulting firms and individual consultants may submit."])
    )
    assert bidder.status.value == "UNSPECIFIED"


@pytest.mark.parametrize("direct", [False, True])
def test_personnel_and_firm_applicant_are_distinct(direct):
    quotes = ["CVs of proposed consultants", "Key expert qualifications"]
    if direct:
        quotes.append("Consulting firms are invited.")
    a = _analysis(
        bidder_eligibility=restriction(quotes, ["CONSULTING_FIRM"]),
        qualification_requirements=["Team leader with MSc", "Two experts"],
        required_documents=["Consultant CVs"],
    )
    assert a.bidder_eligibility.status.value == (
        "RESTRICTED" if direct else "UNSPECIFIED"
    )
    assert a.qualification_requirements == ["Team leader with MSc", "Two experts"]
    assert a.required_documents == ["Consultant CVs"]


def test_strength_is_not_a_trusted_ai_assertion_and_downgrade_is_safe(caplog):
    with caplog.at_level(logging.INFO):
        bidder = BidderEligibility.model_validate(
            restriction(["CV of consultant PRIVATE CONTENT"])
        )
    assert bidder.status.value == "UNSPECIFIED"
    assert "eligibility_evidence_downgraded=true" in caplog.text
    assert "PRIVATE CONTENT" not in caplog.text
    assert BidderEligibility.model_validate(bidder.model_dump()) == bidder
    with pytest.raises(ValidationError):
        BidderEligibility(evidence_strength="FABRICATED")
    with pytest.raises(ValidationError):
        BidderEligibility(extra_field="not permitted")


def test_supporting_label_cannot_establish_restriction():
    bidder = BidderEligibility.model_validate(
        restriction([DIRECT[0][0]], strength="SUPPORTING")
    )
    assert bidder.status.value == "UNSPECIFIED"


def test_direct_quote_still_must_occur_on_page():
    client = Mock()
    client.generate_structured.return_value = _analysis(
        bidder_eligibility=restriction([DIRECT[0][0]])
    )
    with pytest.raises(GeminiResponseError, match="quote is not supported"):
        analyze_opportunity(
            source_url="https://example.test/",
            page_content="CVs required",
            client=client,
        )
    client.generate_structured.assert_called_once()


def test_full_tui_invitation_and_ambiguous_personnel_context():
    quote = "Accordingly, AWF Rwanda invites qualified individual consultants to submit competitive proposals in accordance with the requirements outlined in this Request for Proposals."
    bidder = BidderEligibility.model_validate(
        restriction([quote, "CV(s) of proposed consultant(s)"])
    )
    assert bidder.status.value == "RESTRICTED"
    assert bidder.allowed_applicant_types[0].value == "INDIVIDUAL"
    assert bidder.evidence[0] == quote


def test_ambiguous_evidence_cannot_claim_unrestricted():
    bidder = BidderEligibility(
        status="UNRESTRICTED", evidence=["CV of consultant"], evidence_strength="DIRECT"
    )
    assert bidder.status.value == "UNSPECIFIED"


def test_matching_sanitization_preserves_other_eligibility_requirements():
    raw = _match(
        1,
        score=92,
        strength="VERY_STRONG",
        concerns=["Eligibility requires five years of experience."],
    )
    checked = apply_match_eligibility(raw, _analysis())
    assert (
        "Eligibility requires five years of experience." in checked.eligibility_concerns
    )


def test_awf_eu_ambiguous_extraction_qualifies_and_triages_without_retry(
    monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    quotes = ["Notification of Selected individual", "CV(s) of proposed consultant(s)"]
    backend = Mock()
    backend.generate_structured.return_value = _analysis(
        bidder_eligibility=restriction(quotes)
    )
    a = analyze_opportunity(
        source_url="https://example.test/eu",
        page_content="\n".join(quotes),
        client=backend,
    )
    assert a.bidder_eligibility.status.value == "UNSPECIFIED"
    assert applicant_eligibility_state(a).value == "UNKNOWN"
    oid, companies = _seed(isolated_session_factory, analysis=a)
    matcher = Mock(return_value=_match(companies[0], score=92, strength="VERY_STRONG"))
    analyzer = Mock(return_value=result(bid_readiness="MOSTLY_READY"))
    outcomes = triage_engine.triage_verified_opportunity(
        oid, matcher=matcher, analyzer=analyzer
    )
    assert outcomes[0].status == "TRIAGED" and outcomes[0].error_category is None
    assert outcomes[0].result.bid_readiness.value == "NEEDS_WORK"
    assert outcomes[0].result.eligibility_evidence.value == "UNKNOWN"
    with isolated_session_factory() as db:
        stored = db.scalar(select(CompanyOpportunityMatch))
        assert stored.score == 92 and stored.qualifies_for_feed
    backend.generate_structured.assert_called_once()
    analyzer.assert_called_once()


def test_tui_direct_quote_still_blocks_both_companies(
    monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    a = _analysis(bidder_eligibility=restriction([DIRECT[3][0]]))
    oid, _ = _seed(
        isolated_session_factory, analysis=a, companies=[{"name": "A"}, {"name": "B"}]
    )
    matcher = Mock(
        side_effect=lambda **kw: _match(
            kw["company_id"],
            score=0,
            strength="NOT_A_MATCH",
            concerns=["Individual applicants only"],
        )
    )
    analyzer = Mock(side_effect=AssertionError("No triage for explicit conflict"))
    outcomes = triage_engine.triage_verified_opportunity(
        oid, matcher=matcher, analyzer=analyzer
    )
    assert [o.status for o in outcomes] == ["SKIPPED", "SKIPPED"]
    with isolated_session_factory() as db:
        assert all(
            m.applicant_eligibility == "CONFLICT" and not m.qualifies_for_feed
            for m in db.scalars(select(CompanyOpportunityMatch))
        )
    analyzer.assert_not_called()


@pytest.mark.parametrize(
    "claim",
    [
        "The firm satisfies bidder eligibility.",
        "The company is eligible.",
        "The company is ineligible.",
        "Firms may submit proposals.",
    ],
)
def test_smart_africa_unknown_cannot_be_overridden_by_prose(claim):
    a = _analysis(
        bidder_eligibility=restriction(
            ["Consulting firms are invited."], ["CONSULTING_FIRM"]
        )
    )
    raw = _match(
        1,
        score=92,
        strength="VERY_STRONG",
        explanation="Strong evaluation capability. " + claim,
        strengths=[claim, "Program design"],
        gaps=[claim, "Local delivery evidence is not documented"],
        concerns=[claim],
    )
    checked = apply_match_eligibility(raw, a)
    assert checked.applicant_eligibility.value == "UNKNOWN" and checked.score == 92
    assert checked.explanation == "Strong evaluation capability."
    assert claim not in json.dumps(checked.model_dump(mode="json"))
    assert "requires confirmation" in checked.eligibility_concerns[0]
    assert raw.explanation.endswith(claim)
    assert apply_match_eligibility(checked, a) == checked


def test_capability_contract_preserves_technical_gaps_not_generic_keyword_censorship():
    text = "Develop eligibility assessment software. Eligibility requires five years of experience. Overseas delivery is not documented."
    assert capability_only(text) == text
    assert "describe ONLY" in COMPANY_MATCH_SYSTEM_INSTRUCTION
    assert "compatibility is rendered separately" in COMPANY_MATCH_SYSTEM_INSTRUCTION


@pytest.mark.parametrize("conflict", [False, True])
def test_display_separates_stored_legacy_claims_without_ai_or_writes(
    client, monkeypatch, isolated_session_factory, conflict
):
    _configure(monkeypatch, isolated_session_factory)
    a = _analysis(bidder_eligibility=restriction([DIRECT[3][0]]) if conflict else {})
    oid, companies = _seed(isolated_session_factory, analysis=a)
    matcher = Mock(
        return_value=_match(
            companies[0],
            score=0 if conflict else 92,
            strength="NOT_A_MATCH" if conflict else "VERY_STRONG",
            concerns=["Individual applicants only"] if conflict else [],
        )
    )
    matching_engine.match_verified_opportunity(oid, matcher=matcher)
    claim = "The company satisfies bidder eligibility."
    with isolated_session_factory() as db:
        m = db.scalar(select(CompanyOpportunityMatch))
        m.explanation = "Strong evaluation capability. " + claim
        m.potential_gaps = [claim, "Delivery evidence is not documented."]
        db.commit()
    blocked = Mock(side_effect=AssertionError("No live AI"))
    monkeypatch.setattr(GeminiClient, "generate_structured", blocked)
    for url in [
        f"/?company_id={companies[0]}",
        f"/opportunities/{oid}?company_id={companies[0]}",
    ]:
        response = client.get(url)
        assert response.status_code == 200 and claim not in response.text
        if "/opportunities/" in url or not conflict:
            assert (
                "Applicant eligibility: " + ("CONFLICT" if conflict else "UNKNOWN")
                in response.text
            )
    with isolated_session_factory() as db:
        assert db.scalar(select(CompanyOpportunityMatch)).explanation.endswith(claim)
    blocked.assert_not_called()


def test_analysis_and_match_version_and_upstream_triage_invalidation(monkeypatch):
    assert (
        opportunity_pipeline.OPPORTUNITY_ANALYSIS_VERSION
        == "5-clause-level-applicant-evidence"
    )
    assert TRIAGE_VERSION == "4-hard-requirement-evidence-policy"
    raw = RawOpportunity(
        title="Notice",
        source_url="https://example.test/",
        source_name="Example",
        organisation_name="Example",
        description="CVs of consultants",
    )
    fresh = opportunity_pipeline.build_opportunity_content_fingerprint(raw)
    monkeypatch.setattr(
        opportunity_pipeline,
        "OPPORTUNITY_ANALYSIS_VERSION",
        "3-eligibility-country-quality",
    )
    assert opportunity_pipeline.build_opportunity_content_fingerprint(raw) != fresh
    profile = matching_engine.CompanyProfileInput(1, {"name": "Example"}, 70)
    before = _analysis(bidder_eligibility=restriction([DIRECT[3][0]]))
    after = _analysis(bidder_eligibility=restriction(["CV of consultant"]))

    def fingerprints(a):
        mf = matching_engine.build_match_fingerprint(
            company_profile=profile.profile, opportunity=a, minimum_score=70
        )
        tf = triage_engine.build_triage_fingerprint(
            profile=profile,
            analysis=a,
            match=_match(1, score=92, strength="VERY_STRONG"),
            match_fingerprint=mf,
            deadline=a.deadline,
            status="ACTIVE",
            today=date.today(),
        )
        return mf, tf

    first, second = fingerprints(before), fingerprints(after)
    assert first[0] != second[0] and first[1] != second[1]
    assert (
        matching_engine.MATCH_ANALYSIS_VERSION
        == "7-geography-evidence-grounding"
    )
    monkeypatch.setattr(
        matching_engine, "MATCH_ANALYSIS_VERSION", "3-authoritative-applicant-evidence"
    )
    assert fingerprints(before)[0] != first[0]
    # The new strength enum remains in the provider schema, not an extra-key bypass.
    assert "EligibilityEvidenceStrength" in json.dumps(_analysis().model_json_schema())
    assert CompanyMatch.model_config["extra"] == "forbid"


def test_legacy_weak_restriction_cannot_override_current_display(
    client, monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    a = _analysis(bidder_eligibility=restriction([DIRECT[3][0]]))
    oid, companies = _seed(isolated_session_factory, analysis=a)
    matching_engine.match_verified_opportunity(
        oid,
        matcher=Mock(
            return_value=_match(
                companies[0],
                score=0,
                strength="NOT_A_MATCH",
                concerns=["Individual applicants only"],
            )
        ),
    )
    # Simulate pre-9K persisted extraction without passing through the new model.
    with isolated_session_factory() as db:
        record = db.scalar(select(AIOpportunityAnalysis))
        data = dict(record.analysis_data)
        data["bidder_eligibility"] = restriction(
            ["Notification of Selected individual", "CV(s) of proposed consultant(s)"]
        )
        record.analysis_data = data
        db.commit()
    response = client.get(f"/opportunities/{oid}?company_id={companies[0]}")
    assert response.status_code == 200
    assert "Applicant eligibility: UNKNOWN" in response.text
    assert "Applicant eligibility: CONFLICT" not in response.text
    with isolated_session_factory() as db:
        # Rendering must not fabricate a fresh score or rewrite stored assessments.
        match = db.scalar(select(CompanyOpportunityMatch))
        assert match.score == 0 and match.applicant_eligibility == "CONFLICT"
