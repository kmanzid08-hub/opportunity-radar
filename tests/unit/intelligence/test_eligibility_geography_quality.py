import json
from datetime import date
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.intelligence import matching_engine, triage_engine
from app.intelligence.company_matcher import match_company_to_opportunity
from app.intelligence.countries import normalize_country
from app.intelligence.gemini_client import GeminiResponseError
from app.intelligence.matching_engine import (
    build_company_profile,
    build_match_fingerprint,
)
from app.intelligence.opportunity_analyzer import analyze_opportunity
from app.intelligence.schemas import BidderEligibility
from app.intelligence.triage_analyzer import analyze_triage
from app.intelligence.triage_engine import build_triage_fingerprint, validate_triage
from app.models import (
    AIOpportunityAnalysis,
    CompanyOpportunityMatch,
    CompanyOpportunityTriage,
)

from .test_matching_engine import _analysis, _configure, _match, _seed
from .test_triage import result


def eligibility(types=None, *, status="RESTRICTED"):
    quotes = {
        ("INDIVIDUAL",): "Individual consultants only.",
        ("CONSULTING_FIRM",): "Consulting firms only.",
        (
            "CONSULTING_FIRM",
            "JOINT_VENTURE",
        ): "Consulting firms or joint ventures may apply.",
        ("NGO",): "NGOs only.",
        ("OTHER_ORGANIZATION",): "Registered organizations only.",
        (): "No applicant-type restrictions apply.",
    }
    return {
        "status": status,
        "allowed_applicant_types": types or [],
        "evidence": [quotes[tuple(types or [])]] if status != "UNSPECIFIED" else [],
    }


@pytest.mark.parametrize(
    "types",
    [
        ["INDIVIDUAL"],
        ["CONSULTING_FIRM"],
        ["CONSULTING_FIRM", "JOINT_VENTURE"],
        ["NGO"],
        ["OTHER_ORGANIZATION"],
    ],
)
def test_applicant_restrictions_remain_distinct(types):
    analysis = _analysis(bidder_eligibility=eligibility(types))
    assert (
        analysis.matching_payload()["bidder_eligibility"]["allowed_applicant_types"]
        == types
    )


def test_unspecified_is_not_unrestricted_or_implicitly_permitted():
    unknown = BidderEligibility()
    unrestricted = BidderEligibility.model_validate(eligibility(status="UNRESTRICTED"))
    assert unknown.status != unrestricted.status
    assert unknown.allowed_applicant_types == []
    with pytest.raises(ValidationError):
        BidderEligibility(status="UNRESTRICTED")
    with pytest.raises(ValidationError):
        BidderEligibility(
            status="UNSPECIFIED", allowed_applicant_types=["CONSULTING_FIRM"]
        )
    with pytest.raises(ValidationError):
        BidderEligibility(status="RESTRICTED", evidence=["Quoted text"])


def test_local_registration_is_not_a_guessed_applicant_type():
    analysis = _analysis(
        eligibility_requirements=["Locally registered entities"],
        eligibility_countries=["Rwanda"],
    )
    assert analysis.bidder_eligibility.status.value == "UNSPECIFIED"
    assert analysis.eligibility_countries == ["Rwanda"]


def test_explicit_conflict_reaches_matcher_and_cannot_be_accepted_as_compatible():
    analysis = _analysis(bidder_eligibility=eligibility(["INDIVIDUAL"]))
    client = Mock()
    client.generate_structured.return_value = _match(1, score=95, strength="STRONG")
    with pytest.raises(GeminiResponseError, match="individual-only"):
        match_company_to_opportunity(
            company_id=1,
            company_profile={"services": ["Evaluation"]},
            opportunity=analysis,
            client=client,
        )
    prompt = client.generate_structured.call_args.args[0]
    assert '"individual_only_conflict": true' in prompt
    assert '"allowed_applicant_types": ["INDIVIDUAL"]' in prompt
    instruction = client.generate_structured.call_args.kwargs["system_instruction"]
    assert (
        "Never\n    broaden individual consultants" in instruction
        or "broaden individual consultants" in instruction
    )
    assert "NOT proof" in instruction
    assert "do not narrow" in instruction


@pytest.mark.parametrize(
    "explicit, strength, expected",
    [
        (True, "STRONG", "ERROR"),
        (True, "NOT_A_MATCH", "MATCHED"),
        (False, "STRONG", "MATCHED"),
    ],
)
def test_hard_conflict_feed_guard_without_unknown_rejection(
    monkeypatch, isolated_session_factory, explicit, strength, expected
):
    _configure(monkeypatch, isolated_session_factory)
    analysis = _analysis(
        bidder_eligibility=eligibility(["INDIVIDUAL"]) if explicit else {}
    )
    opportunity_id, companies = _seed(isolated_session_factory, analysis=analysis)
    matcher = Mock(
        return_value=_match(
            companies[0],
            score=95,
            strength=strength,
            concerns=["Applicant eligibility requires confirmation or conflicts"],
        )
    )
    outcome = matching_engine.match_verified_opportunity(
        opportunity_id, matcher=matcher
    )[0]
    assert outcome.status.value == expected
    assert outcome.qualifies_for_feed is (not explicit)
    with isolated_session_factory() as db:
        record = db.scalar(select(CompanyOpportunityMatch))
        assert record.qualifies_for_feed is (not explicit)
        if expected == "ERROR":
            assert record.error_reason and record.score is None


def test_analyzer_requires_a_supported_quote_not_invented_permissions():
    client = Mock()
    client.generate_structured.return_value = _analysis(
        bidder_eligibility=eligibility(["INDIVIDUAL"])
    )
    kwargs = dict(source_url="https://example.test/notice", client=client)
    valid = analyze_opportunity(
        page_content="Individual consultants only. Submit a CV.", **kwargs
    )
    assert valid.bidder_eligibility.allowed_applicant_types[0].value == "INDIVIDUAL"
    with pytest.raises(GeminiResponseError, match="quote is not supported"):
        analyze_opportunity(page_content="No applicant type specified.", **kwargs)


@pytest.mark.parametrize("readiness", ["UNKNOWN", "NEEDS_WORK"])
def test_unknown_eligibility_has_schema_valid_conservative_triage(readiness):
    analysis = _analysis(eligibility_requirements=[])
    candidate = result(bid_readiness=readiness, eligibility_evidence="UNKNOWN")
    checked = validate_triage(
        candidate,
        analysis=analysis,
        match=_match(1, score=95, strength="STRONG"),
        minimum_score=70,
        deadline=analysis.deadline,
        status="ACTIVE",
    )
    assert checked.bid_readiness.value == readiness


@pytest.mark.parametrize("readiness", ["READY", "MOSTLY_READY"])
@pytest.mark.parametrize("requirements", [[], ["Registration requires confirmation"]])
def test_unknown_hard_eligibility_still_forbids_ready_states(readiness, requirements):
    analysis = _analysis(eligibility_requirements=requirements)
    with pytest.raises(ValueError, match="eligibility"):
        validate_triage(
            result(bid_readiness=readiness, eligibility_evidence="UNKNOWN"),
            analysis=analysis,
            match=_match(1, score=95, strength="STRONG"),
            minimum_score=70,
            deadline=analysis.deadline,
            status="ACTIVE",
        )


def test_triage_prompt_exposes_exact_missing_requirement_constraint():
    analysis = _analysis(eligibility_requirements=[])
    client = Mock(return_value=None)
    client.generate_structured.return_value = result(
        bid_readiness="UNKNOWN", eligibility_evidence="UNKNOWN"
    )
    analyze_triage(
        company_profile={"name": "Example"},
        opportunity=analysis,
        match=_match(1, score=95, strength="STRONG"),
        effective_deadline=analysis.deadline,
        effective_status="ACTIVE",
        as_of_date=date(2026, 10, 6),
        client=client,
    )
    payload = json.loads(client.generate_structured.call_args.args[0])
    assert payload["eligibility_context"]["eligibility_requirements_missing"] is True
    instruction = client.generate_structured.call_args.kwargs["system_instruction"]
    assert "eligibility_evidence MUST be UNKNOWN" in instruction
    assert "never READY or MOSTLY_READY" in instruction


def test_changed_eligibility_invalidates_match_and_triage_fingerprints():
    before = _analysis()
    after = _analysis(bidder_eligibility=eligibility(["INDIVIDUAL"]))
    profile = matching_engine.CompanyProfileInput(1, {"name": "Example"}, 70)

    def matching(analysis):
        return build_match_fingerprint(
            company_profile=profile.profile, opportunity=analysis, minimum_score=70
        )

    def triage(analysis):
        return build_triage_fingerprint(
            profile=profile,
            analysis=analysis,
            match=_match(1, score=95, strength="STRONG"),
            match_fingerprint="unchanged",
            deadline=analysis.deadline,
            status="ACTIVE",
            today=date(2026, 10, 6),
        )

    assert matching(before) != matching(after)
    assert triage(before) != triage(after)


def test_policy_adjusted_eligibility_is_a_valid_success_cache(
    monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    # Temporary diagnostic for the regression test: expose synthetic fixture
    # failures instead of recording a generic status.
    def reveal_synthetic_failure(exc, **_kwargs):
        raise exc
    monkeypatch.setattr(triage_engine, "record_failure", reveal_synthetic_failure)
    opportunity_id, companies = _seed(
        isolated_session_factory, analysis=_analysis(eligibility_requirements=[])
    )
    matcher = Mock(return_value=_match(companies[0], score=95, strength="STRONG"))
    analyzer = Mock(
        side_effect=[
            result(),
            result(bid_readiness="UNKNOWN", eligibility_evidence="UNKNOWN"),
        ]
    )
    assert (
        triage_engine.triage_verified_opportunity(
            opportunity_id, matcher=matcher, analyzer=analyzer
        )[0].status
        == "TRIAGED"
    )
    with isolated_session_factory() as db:
        record = db.scalar(select(CompanyOpportunityTriage))
        assert record.result_data["bid_readiness"] == "NEEDS_WORK"
        assert record.result_data["eligibility_evidence"] == "UNKNOWN"
        assert record.error_reason is None
    assert (
        triage_engine.triage_verified_opportunity(
            opportunity_id, matcher=matcher, analyzer=analyzer
        )[0].status
        == "CACHED"
    )
    assert analyzer.call_count == 1
    matcher.assert_called_once()


@pytest.mark.parametrize(
    "raw, canonical",
    [
        ("Côte d'Ivoire", "Côte d’Ivoire"),
        ("Ivory Coast", "Côte d’Ivoire"),
        ("DR Congo", "Democratic Republic of the Congo"),
        ("Democratic Republic of the Congo", "Democratic Republic of the Congo"),
        ("Tanzania", "Tanzania"),
        ("United Republic of Tanzania", "Tanzania"),
        ("Kigali", None),
        ("Applicants should demonstrate...", None),
    ],
)
def test_exact_country_aliases_not_city_or_prose(raw, canonical):
    assert normalize_country(raw) == canonical


def test_invalid_countries_removed_deduplicated_and_safely_diagnosed(caplog, monkeypatch):
    from app.intelligence import countries

    # Migration tests configure Alembic logging and disable existing loggers.
    monkeypatch.setattr(countries.logger, "disabled", False)
    analysis = _analysis(
        buyer_country="Kigali",
        delivery_countries=[
            "Rwanda",
            "Kigali",
            "Applicants should demonstrate CONFIDENTIAL",
            "rwanda",
        ],
        eligibility_countries=["Ivory Coast", "Côte d'Ivoire", "Kigali"],
    )
    assert analysis.buyer_country is None
    assert analysis.delivery_countries == ["Rwanda"]
    assert analysis.eligibility_countries == ["Côte d’Ivoire"]
    assert analysis.geographic_scope.value == "SINGLE_COUNTRY"
    assert "rejected_count=2" in caplog.text
    assert "CONFIDENTIAL" not in caplog.text and "Kigali" not in caplog.text


@pytest.mark.parametrize("scope", ["REGIONAL", "GLOBAL", "REMOTE", "UNKNOWN"])
def test_empty_noncountry_scopes_remain_meaningful(scope):
    analysis = _analysis(delivery_countries=["Kigali"], geographic_scope=scope)
    assert analysis.delivery_countries == []
    assert analysis.geographic_scope.value == scope
    assert analysis.country is None


@pytest.mark.parametrize("scope", ["SINGLE_COUNTRY", "MULTI_COUNTRY"])
def test_declared_country_scope_with_no_valid_country_is_not_silently_valid(scope):
    with pytest.raises(ValidationError, match="requires valid delivery countries"):
        _analysis(delivery_countries=["Kigali"], geographic_scope=scope)


def test_valid_multicountry_and_single_country_scopes_are_consistent():
    assert (
        _analysis(delivery_countries=["Rwanda", "Ghana"]).geographic_scope.value
        == "MULTI_COUNTRY"
    )
    single = _analysis(
        delivery_countries=["Rwanda", "Kigali"], geographic_scope="MULTI_COUNTRY"
    )
    assert single.geographic_scope.value == "SINGLE_COUNTRY"
    for scope in ["REGIONAL", "GLOBAL"]:
        assert (
            _analysis(
                delivery_countries=["Rwanda"], geographic_scope=scope
            ).geographic_scope.value
            == scope
        )


def test_search_context_does_not_repair_invalid_extracted_country():
    client = Mock()
    client.generate_structured.return_value = _analysis(delivery_countries=["Kigali"])
    analyzed = analyze_opportunity(
        source_url="https://example.test/notice",
        page_content="A notice",
        country_hint="Rwanda",
        client=client,
    )
    assert analyzed.delivery_countries == [] and analyzed.country is None


def test_company_preferences_remain_distinct_from_proven_geography():
    from app.models import Organization, OpportunityPreference

    organization = Organization(id=1, name="Example", country="Rwanda")
    preference = OpportunityPreference(
        countries=["Ghana", "Africa-wide willingness"],
        regions=["East Africa", "Sub-Saharan Africa"],
        industries=[],
        services=[],
        opportunity_types=[],
        keywords=[],
        languages=[],
        intelligence={},
        minimum_match_score=70,
    )
    profile = build_company_profile(organization, preference).profile
    geography = profile["geography"]
    assert geography["base_country"] == "Rwanda"
    assert geography["preferred_countries"] == ["Ghana"]
    assert geography["preferred_regions"] == ["East Africa", "Sub-Saharan Africa"]
    assert geography["unstructured_geographic_willingness"] == [
        "Africa-wide willingness"
    ]
    assert geography["demonstrated_delivery_countries"] is None
    assert preference.countries == ["Ghana", "Africa-wide willingness"]
    assert organization.country == "Rwanda"


def test_eligibility_change_rematches_a_stored_pair(
    monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, companies = _seed(isolated_session_factory)
    matcher = Mock(
        side_effect=[
            _match(companies[0], score=95, strength="STRONG"),
            _match(
                companies[0],
                score=10,
                strength="NOT_A_MATCH",
                concerns=["Individuals only"],
            ),
        ]
    )
    assert matching_engine.match_verified_opportunity(opportunity_id, matcher=matcher)[
        0
    ].qualifies_for_feed
    with isolated_session_factory() as db:
        record = db.scalar(select(AIOpportunityAnalysis))
        record.analysis_data = _analysis(
            bidder_eligibility=eligibility(["INDIVIDUAL"])
        ).model_dump(mode="json")
        db.commit()
    assert not matching_engine.match_verified_opportunity(
        opportunity_id, matcher=matcher
    )[0].qualifies_for_feed
    assert matcher.call_count == 2
