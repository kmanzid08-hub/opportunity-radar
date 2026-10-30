from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.intelligence import matching_engine
from app.intelligence.gap_evidence import normalize_company_gap, normalize_match_gaps
from app.intelligence.gemini_client import GeminiClient
from app.intelligence.match_store import match_is_current
from app.intelligence.opportunity_pipeline import OPPORTUNITY_ANALYSIS_VERSION
from app.intelligence.triage_analyzer import TRIAGE_VERSION
from app.models import CompanyOpportunityMatch

from .test_matching_engine import _analysis, _configure, _match, _seed


SAMA_GAP = (
    "Primary past project delivery is concentrated within Rwanda rather than "
    "multi-country regional implementation."
)


@pytest.fixture
def incomplete_profile():
    return {
        "home_country": "Rwanda",
        "geography": {
            "base_country": "Rwanda",
            "preferred_countries": ["Rwanda", "Ghana", "Kenya"],
            "preferred_regions": ["Africa"],
            "unstructured_geographic_willingness": ["Willing to deliver across Africa"],
            "demonstrated_delivery_countries": None,
        },
        "experience": {
            "relevant_experience": ["Policy research assignment in Rwanda"],
            "track_record": ["Institutional assessment in Rwanda"],
        },
    }


@pytest.mark.parametrize(
    "text",
    [
        SAMA_GAP,
        "Experience is primarily in Rwanda.",
        "The firm's track record is mainly Rwanda-based.",
        "Operations are largely domestic.",
        "Most experience is domestic.",
        "The company has primarily domestic experience.",
        "Its footprint is predominantly in Rwanda.",
        "Delivery footprint is concentrated in Rwanda.",
        "Most projects have been delivered in Rwanda.",
        "Profile shows limited international delivery.",
        "The company operates mainly in Rwanda.",
        "The company lacks international experience.",
        "Experience is only domestic.",
        "The company has domestic-only experience.",
        "Experience is primarily in Rwanda, with foreign experience unconfirmed.",
    ],
)
def test_unsupported_geographic_variants(text, incomplete_profile):
    before = deepcopy(incomplete_profile)
    normalized = normalize_company_gap(text, incomplete_profile)
    assert normalized != text
    assert "requires confirmation" in normalized or "not documented" in normalized
    assert "concentrated" not in normalized
    assert normalize_company_gap(normalized, incomplete_profile) == normalized
    assert incomplete_profile == before


def test_single_country_project_examples_do_not_prove_distribution(incomplete_profile):
    incomplete_profile["geography"]["demonstrated_delivery_countries"] = ["Rwanda"]
    assert "requires confirmation" in normalize_company_gap(
        SAMA_GAP, incomplete_profile
    )
    # Even a larger sample is not an exhaustive history or a distribution fact.
    incomplete_profile["experience"]["track_record"] *= 30
    assert "requires confirmation" in normalize_company_gap(
        SAMA_GAP, incomplete_profile
    )


@pytest.mark.parametrize(
    "fact,text",
    [
        ("We only operate in Rwanda.", "The company operates only in Rwanda."),
        ("We only operate in Rwanda.", "Operations are largely domestic."),
        (
            "We do not undertake assignments outside Rwanda.",
            "Operations are limited to Rwanda.",
        ),
        ("Past work is explicitly limited to Rwanda.", SAMA_GAP),
        (
            "Experience is primarily in Rwanda.",
            "Its experience is primarily in Rwanda.",
        ),
        (
            "Profile shows limited international delivery.",
            "Limited international delivery.",
        ),
    ],
)
def test_explicit_geographic_facts_preserved(fact, text, incomplete_profile):
    incomplete_profile["description"] = fact
    assert normalize_company_gap(text, incomplete_profile) == text


def test_operations_restriction_is_not_evidence_of_history(incomplete_profile):
    incomplete_profile["description"] = "We only operate in Rwanda."
    assert "requires confirmation" in normalize_company_gap(
        SAMA_GAP, incomplete_profile
    )


@pytest.mark.parametrize(
    "fact",
    [
        "Experience is primarily in Rwanda.",
        "Experience is concentrated in Rwanda.",
    ],
)
def test_concentration_is_not_proof_of_exclusivity(incomplete_profile, fact):
    incomplete_profile["experience"]["track_record"] = [fact]
    assert "not documented" in normalize_company_gap(
        "Experience is limited to Rwanda.", incomplete_profile
    )


def test_limited_international_experience_does_not_prove_absence(incomplete_profile):
    incomplete_profile["experience"]["track_record"] = [
        "Limited international experience."
    ]
    assert "requires confirmation" in normalize_company_gap(
        "The company lacks international experience.", incomplete_profile
    )
    assert (
        normalize_company_gap("Limited international experience.", incomplete_profile)
        == "Limited international experience."
    )


def test_strength_scope_is_not_inferred_from_a_different_region(incomplete_profile):
    incomplete_profile["experience"]["track_record"] = ["Proven Europe-wide delivery."]
    raw = _match(
        1, score=75, strength="STRONG", strengths=["Proven Africa-wide delivery."]
    )
    assert (
        "requires confirmation"
        in normalize_match_gaps(raw, incomplete_profile).strengths[0]
    )


def test_operations_uncertainty_is_not_rephrased_as_delivery_history():
    assert normalize_company_gap("Operations are limited to Rwanda.", {}) == (
        "Geographic scope of operations requires confirmation from the stored profile."
    )


def test_explicit_exclusion_preserved_without_inventing_negative_history(
    incomplete_profile,
):
    incomplete_profile["exclusions"] = {"countries": ["Ghana"]}
    claim = "The company does not operate in Ghana."
    assert normalize_company_gap(claim, incomplete_profile) == claim
    assert "not documented" in normalize_company_gap(
        "No experience in Ghana.", incomplete_profile
    )
    assert "requires confirmation" in normalize_company_gap(
        SAMA_GAP, incomplete_profile
    )


@pytest.mark.parametrize(
    "strength",
    [
        "Demonstrated regional experience.",
        "Proven Africa-wide delivery.",
        "Strong multi-country track record.",
        "The firm has extensive international experience.",
        "Delivery footprint is concentrated in Rwanda.",
    ],
)
def test_strengths_cannot_promote_preference_or_willingness(
    strength, incomplete_profile
):
    # Repeating the AI claim in preference/willingness fields is not evidence.
    incomplete_profile["stated_preferences"] = [strength]
    incomplete_profile["geography"]["unstructured_geographic_willingness"] = [strength]
    raw = _match(
        1,
        score=75,
        strength="STRONG",
        strengths=[strength, "Strong policy analysis capability."],
    )
    normalized = normalize_match_gaps(raw, incomplete_profile)
    assert normalized.strengths[0] != strength
    assert (
        "confirmation" in normalized.strengths[0]
        or "not documented" in normalized.strengths[0]
    )
    assert normalized.strengths[1] == raw.strengths[1]


def test_supported_multi_country_does_not_prove_continent_wide(incomplete_profile):
    incomplete_profile["geography"]["demonstrated_delivery_countries"] = [
        "Rwanda",
        "Ghana",
    ]
    raw = _match(
        1,
        score=75,
        strength="STRONG",
        strengths=[
            "Strong multi-country track record.",
            "Proven Africa-wide delivery.",
        ],
    )
    normalized = normalize_match_gaps(raw, incomplete_profile)
    assert normalized.strengths[0] == raw.strengths[0]
    assert "requires confirmation" in normalized.strengths[1]
    assert "requires confirmation" in normalize_company_gap(
        SAMA_GAP, incomplete_profile
    )


def test_explicit_delivery_fact_preserves_geographic_strength(incomplete_profile):
    claim = "Demonstrated regional experience."
    incomplete_profile["experience"]["track_record"] = [claim]
    raw = _match(1, score=75, strength="STRONG", strengths=[claim])
    assert normalize_match_gaps(raw, incomplete_profile).strengths == [claim]


@pytest.mark.parametrize(
    "text",
    [
        "The company is based in Rwanda.",
        "Preferred delivery countries include Ghana and Kenya.",
        "The firm is willing to operate across Africa.",
        "The company operates in Rwanda.",
        "Regional implementation experience requires confirmation.",
        '"Experience is primarily in Rwanda."',
        "The opportunity requires regional delivery experience.",
        "Firms must demonstrate regional experience.",
        "No experience in Ghana is required.",
    ],
)
def test_out_of_scope_and_qualified_text_unchanged(text, incomplete_profile):
    assert normalize_company_gap(text, incomplete_profile) == text


def test_wording_only_and_idempotent(incomplete_profile):
    raw = _match(
        1,
        score=75,
        strength="STRONG",
        gaps=[SAMA_GAP],
        explanation=SAMA_GAP,
        strengths=["Proven Africa-wide delivery."],
        concerns=["Applicants must be registered."],
    )
    before = raw.model_dump()
    normalized = normalize_match_gaps(raw, incomplete_profile)
    for key in before.keys() - {"potential_gaps", "explanation", "strengths"}:
        assert normalized.model_dump()[key] == before[key]
    assert raw.model_dump() == before
    assert normalize_match_gaps(normalized, incomplete_profile) == normalized


def test_geographic_wording_persists_and_caches_without_changing_qualification(
    monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    oid, companies = _seed(isolated_session_factory)
    raw = _match(
        companies[0],
        score=75,
        strength="STRONG",
        gaps=[SAMA_GAP],
        strengths=["Proven Africa-wide delivery."],
    )
    generator = Mock(return_value=raw)
    first = matching_engine.match_verified_opportunity(oid, matcher=generator)[0]
    second = matching_engine.match_verified_opportunity(oid, matcher=generator)[0]
    assert (
        first.match.score == raw.score
        and first.match.match_strength == raw.match_strength
    )
    assert first.qualifies_for_feed and second.qualifies_for_feed
    assert "requires confirmation" in first.match.potential_gaps[0]
    assert "requires confirmation" in first.match.strengths[0]
    assert second.status.value == "CACHED" and second.match == first.match
    with isolated_session_factory() as db:
        stored = db.scalar(select(CompanyOpportunityMatch))
        assert stored.potential_gaps == first.match.potential_gaps
        assert stored.strengths == first.match.strengths
    generator.assert_called_once()


def test_v6_stales_naturally_and_render_never_refreshes(
    monkeypatch, isolated_session_factory, client
):
    _configure(monkeypatch, isolated_session_factory)
    analysis = _analysis(
        deadline=datetime.now(timezone.utc).date() + timedelta(days=20)
    )
    oid, companies = _seed(isolated_session_factory, analysis=analysis)
    cid = companies[0]
    matcher = Mock(return_value=_match(cid, score=85, strength="STRONG"))
    current_version = matching_engine.MATCH_ANALYSIS_VERSION
    assert current_version == "7-geography-evidence-grounding"
    with monkeypatch.context() as old:
        old.setattr(
            matching_engine, "MATCH_ANALYSIS_VERSION", "6-evidence-aware-company-gaps"
        )
        matching_engine.match_verified_opportunity(oid, matcher=matcher)
        with isolated_session_factory() as db:
            old_fingerprint = db.scalar(
                select(CompanyOpportunityMatch)
            ).match_fingerprint
    profile = matching_engine.load_active_company_profiles([cid])[0]
    with isolated_session_factory() as db:
        stored = db.scalar(select(CompanyOpportunityMatch))
        assert not match_is_current(stored, profile=profile, analysis=analysis)
    forbidden = Mock(side_effect=AssertionError("No AI/network during rendering"))
    monkeypatch.setattr(GeminiClient, "generate_structured", forbidden)
    monkeypatch.setattr(GeminiClient, "generate_text", forbidden)
    import requests

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    for params in ({}, {"company_id": cid}):
        assert not client.get("/", params=params).context["opportunities"]
    detail = client.get(f"/opportunities/{oid}", params={"company_id": cid})
    assert detail.status_code == 200
    assert detail.context["stored_match"] is None
    assert detail.context["match_needs_refresh"]
    assert "Match assessment needs refresh" in detail.text
    assert detail.context["opportunity"].title == analysis.title
    with isolated_session_factory() as db:
        stored = db.scalar(select(CompanyOpportunityMatch))
        assert stored.match_fingerprint == old_fingerprint and stored.score == 85
    forbidden.assert_not_called()
    assert OPPORTUNITY_ANALYSIS_VERSION == "5-clause-level-applicant-evidence"
    assert TRIAGE_VERSION == "4-hard-requirement-evidence-policy"
