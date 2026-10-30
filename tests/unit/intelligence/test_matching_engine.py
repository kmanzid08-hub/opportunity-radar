from __future__ import annotations

from collections.abc import Iterable

import pytest
from sqlalchemy import func, select

from app.intelligence import matching_engine
from app.company_profiles import CompanyIntelligence
from app.intelligence.gemini_client import GeminiAPIError
from app.intelligence.matching_engine import (
    CompanyMatchProcessingStatus,
    match_verified_opportunity,
)
from app.intelligence.opportunity_pipeline import (
    build_semantic_opportunity_fingerprint,
)
from app.intelligence.schemas import CompanyMatch, OpportunityAnalysis
from app.models import (
    AIOpportunityAnalysis,
    CompanyOpportunityMatch,
    Opportunity,
    OpportunityPreference,
    Organization,
)


class RecordingMatcher:
    def __init__(self, responses: dict[int, Iterable[object]]) -> None:
        self.responses = {
            company_id: iter(values) for company_id, values in responses.items()
        }
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        company_id = int(str(kwargs["company_id"]))
        response = next(self.responses[company_id])
        if isinstance(response, Exception):
            raise response
        return response


def _analysis(**overrides: object) -> OpportunityAnalysis:
    values: dict[str, object] = {
        "decision": "ACCEPT",
        "confidence": 94,
        "reason": "A specific live professional-services opportunity.",
        "title": "Independent assessment of financial and project controls",
        "buyer_name": "Example Development Programme",
        "issuing_organisation": "Example Development Programme",
        "country": "Rwanda",
        "location": "Kigali",
        "opportunity_type": "PROFESSIONAL_SERVICE",
        "status": "ACTIVE",
        "deadline": "2026-10-30",
        "summary": "Independent assessment of programme financial controls.",
        "scope": "Assess financial statements and project control systems.",
        "services_required": ["Financial controls assessment"],
        "eligibility_requirements": ["Registered professional firm"],
        "experience_requirements": [
            "At least ten years of experience",
            "Three similar assignments",
        ],
        "deliverables": ["Independent assessment report"],
        "original_source_url": "https://example.test/opportunities/1",
        "evidence": ["Qualified firms are invited to submit proposals."],
    }
    values.update(overrides)
    return OpportunityAnalysis.model_validate(values)


def _match(
    company_id: int,
    *,
    score: int,
    strength: str,
    explanation: str = "Evidence-based semantic fit assessment.",
    strengths: list[str] | None = None,
    gaps: list[str] | None = None,
    concerns: list[str] | None = None,
) -> CompanyMatch:
    return CompanyMatch(
        company_id=company_id,
        score=score,
        match_strength=strength,
        explanation=explanation,
        strengths=strengths or [],
        potential_gaps=gaps or [],
        eligibility_concerns=concerns or [],
        recommendation="Pursue if the identified gaps can be confirmed.",
    )


def _seed(
    isolated_session_factory,
    *,
    analysis: OpportunityAnalysis | None = None,
    companies: list[dict[str, object]] | None = None,
) -> tuple[int, list[int]]:
    analysis = analysis or _analysis()
    companies = companies or [
        {
            "name": "UT CPA Ltd",
            "description": "Independent assurance for development programmes.",
            "industry": "Professional services",
            "country": "Rwanda",
            "services": ["External audit", "Financial controls assessment"],
            "industries": ["International development"],
            "countries": ["Rwanda", "Uganda"],
            "opportunity_types": ["Professional services", "Consultancy"],
            "keywords": ["Donor-funded programmes"],
            "minimum_match_score": 70,
        }
    ]

    with isolated_session_factory() as db:
        opportunity = Opportunity(
            organisation_name=analysis.issuing_organisation,
            title=analysis.title or "Analyzed opportunity",
            category=analysis.opportunity_type.value,
            description=analysis.summary,
            deadline=analysis.deadline,
            source_name="Example Portal",
            source_url="https://example.test/opportunities/1",
        )
        db.add(opportunity)
        db.flush()
        analysis_record = AIOpportunityAnalysis(
            opportunity_id=opportunity.id,
            source_url=opportunity.source_url,
            raw_title=opportunity.title,
            source_name=opportunity.source_name,
            content_fingerprint="a" * 64,
            opportunity_fingerprint=build_semantic_opportunity_fingerprint(analysis),
            decision=analysis.decision.value,
            confidence=analysis.confidence,
            opportunity_type=analysis.opportunity_type.value,
            opportunity_status=analysis.status.value,
            analysis_data=analysis.model_dump(mode="json"),
            analyzed_at=matching_engine._utc_now(),
        )
        db.add(analysis_record)

        company_ids: list[int] = []
        for values in companies:
            organization = Organization(
                name=str(values["name"]),
                description=str(values.get("description", "")) or None,
                industry=str(values.get("industry", "")) or None,
                country=str(values.get("country", "")) or None,
                is_active=True,
            )
            db.add(organization)
            db.flush()
            preference = OpportunityPreference(
                organization_id=organization.id,
                countries=list(values.get("countries", [])),
                regions=list(values.get("regions", [])),
                industries=list(values.get("industries", [])),
                services=list(values.get("services", [])),
                opportunity_types=list(values.get("opportunity_types", [])),
                keywords=list(values.get("keywords", [])),
                languages=list(values.get("languages", [])),
                minimum_match_score=int(values.get("minimum_match_score", 70)),
            )
            db.add(preference)
            company_ids.append(organization.id)
        db.commit()
        return opportunity.id, company_ids


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    monkeypatch.delenv("COMPANY_MATCH_MINIMUM_SCORE", raising=False)
    monkeypatch.setattr(
        matching_engine,
        "SessionLocal",
        isolated_session_factory,
    )


def test_strong_audit_match_is_persisted_and_enters_feed(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(isolated_session_factory)
    company_id = company_ids[0]
    matcher = RecordingMatcher(
        {
            company_id: [
                _match(
                    company_id,
                    score=94,
                    strength="VERY_STRONG",
                    explanation=(
                        "Strong fit because the assignment requires independent "
                        "financial-controls assurance for a donor programme."
                    ),
                    strengths=["Relevant assurance service and market"],
                )
            ]
        }
    )

    outcomes = match_verified_opportunity(opportunity_id, matcher=matcher)

    assert outcomes[0].qualifies_for_feed is True
    assert outcomes[0].match is not None
    assert outcomes[0].match.score == 94
    with isolated_session_factory() as db:
        record = db.scalar(select(CompanyOpportunityMatch))
        assert record is not None
        assert record.score == 94
        assert record.qualifies_for_feed is True
        assert record.minimum_score == 70


def test_strong_consulting_evaluation_match(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    analysis = _analysis(
        title="Evaluation of a national climate adaptation programme",
        opportunity_type="CONSULTANCY",
        services_required=["Programme evaluation", "Climate research"],
        scope="Evaluate outcomes of the national adaptation programme.",
    )
    opportunity_id, company_ids = _seed(
        isolated_session_factory,
        analysis=analysis,
        companies=[
            {
                "name": "UT Consulting",
                "description": "Programme evaluation and climate research.",
                "industry": "Environmental consulting",
                "country": "Rwanda",
                "services": ["Monitoring and evaluation", "Research"],
                "industries": ["Climate adaptation"],
                "countries": ["Rwanda"],
                "opportunity_types": ["Consultancy"],
            }
        ],
    )
    company_id = company_ids[0]

    outcome = match_verified_opportunity(
        opportunity_id,
        matcher=RecordingMatcher(
            {company_id: [_match(company_id, score=91, strength="VERY_STRONG")]}
        ),
    )[0]

    assert outcome.qualifies_for_feed is True
    assert outcome.match is not None
    assert outcome.match.score == 91


@pytest.mark.parametrize(
    ("case", "score", "strength", "gaps", "concerns"),
    [
        ("weak unrelated opportunity", 12, "NOT_A_MATCH", [], []),
        ("geography mismatch", 28, "WEAK", ["Target market is unsupported"], []),
        ("opportunity type mismatch", 35, "WEAK", ["Assignment type is excluded"], []),
        (
            "eligibility gap",
            72,
            "STRONG",
            ["Required ten years and three similar assignments are not confirmed"],
            [],
        ),
        (
            "missing company information",
            45,
            "MODERATE",
            ["Company capabilities and experience are unknown"],
            [],
        ),
    ],
)
def test_match_assessments_retain_low_scores_and_unknown_gaps(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
    case: str,
    score: int,
    strength: str,
    gaps: list[str],
    concerns: list[str],
) -> None:
    del case
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(isolated_session_factory)
    company_id = company_ids[0]

    outcome = match_verified_opportunity(
        opportunity_id,
        matcher=RecordingMatcher(
            {
                company_id: [
                    _match(
                        company_id,
                        score=score,
                        strength=strength,
                        gaps=gaps,
                        concerns=concerns,
                    )
                ]
            }
        ),
    )[0]

    assert outcome.qualifies_for_feed is (score >= 70)
    assert outcome.match is not None
    assert outcome.match.potential_gaps == gaps
    with isolated_session_factory() as db:
        record = db.scalar(select(CompanyOpportunityMatch))
        assert record is not None
        assert record.score == score


def test_job_vacancy_is_not_sent_to_company_matcher(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(
        isolated_session_factory,
        analysis=_analysis(
            decision="REJECT",
            title="Audit Manager - Kigali",
            opportunity_type="JOB_VACANCY",
            status="UNKNOWN",
        ),
    )
    matcher = RecordingMatcher(
        {company_ids[0]: [_match(company_ids[0], score=95, strength="VERY_STRONG")]}
    )

    with pytest.raises(ValueError, match="VERIFIED opportunities only"):
        match_verified_opportunity(opportunity_id, matcher=matcher)

    assert matcher.calls == []


@pytest.mark.parametrize("scores", [(92, 81), (42, 18)])
def test_multiple_companies_are_matched_independently(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
    scores: tuple[int, int],
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(
        isolated_session_factory,
        companies=[
            {
                "name": "First Company",
                "services": ["Assurance"],
                "countries": ["Rwanda"],
            },
            {
                "name": "Second Company",
                "services": ["Programme evaluation"],
                "countries": ["Rwanda"],
            },
        ],
    )
    matcher = RecordingMatcher(
        {
            company_ids[0]: [
                _match(
                    company_ids[0],
                    score=scores[0],
                    strength="STRONG" if scores[0] >= 70 else "WEAK",
                )
            ],
            company_ids[1]: [
                _match(
                    company_ids[1],
                    score=scores[1],
                    strength="STRONG" if scores[1] >= 70 else "NOT_A_MATCH",
                )
            ],
        }
    )

    outcomes = match_verified_opportunity(opportunity_id, matcher=matcher)

    assert [item.company_id for item in outcomes] == company_ids
    assert [item.qualifies_for_feed for item in outcomes] == [
        score >= 70 for score in scores
    ]
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(CompanyOpportunityMatch)) == 2


def test_identical_inputs_reuse_cached_match(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(isolated_session_factory)
    company_id = company_ids[0]
    matcher = RecordingMatcher(
        {company_id: [_match(company_id, score=88, strength="STRONG")]}
    )

    first = match_verified_opportunity(opportunity_id, matcher=matcher)[0]
    second = match_verified_opportunity(opportunity_id, matcher=matcher)[0]

    assert first.status is CompanyMatchProcessingStatus.MATCHED
    assert second.status is CompanyMatchProcessingStatus.CACHED
    assert len(matcher.calls) == 1


def test_company_threshold_controls_feed_without_discarding_match(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(
        isolated_session_factory,
        companies=[
            {
                "name": "Selective Advisory",
                "services": ["Financial controls assessment"],
                "countries": ["Rwanda"],
                "minimum_match_score": 85,
            }
        ],
    )
    company_id = company_ids[0]

    outcome = match_verified_opportunity(
        opportunity_id,
        matcher=RecordingMatcher(
            {company_id: [_match(company_id, score=82, strength="STRONG")]}
        ),
    )[0]

    assert outcome.qualifies_for_feed is False
    with isolated_session_factory() as db:
        record = db.scalar(select(CompanyOpportunityMatch))
        assert record is not None
        assert record.score == 82
        assert record.minimum_score == 85


def test_profile_change_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(isolated_session_factory)
    company_id = company_ids[0]
    matcher = RecordingMatcher(
        {
            company_id: [
                _match(company_id, score=75, strength="STRONG"),
                _match(company_id, score=90, strength="VERY_STRONG"),
            ]
        }
    )

    match_verified_opportunity(opportunity_id, matcher=matcher)
    with isolated_session_factory() as db:
        preference = db.scalar(
            select(OpportunityPreference).where(
                OpportunityPreference.organization_id == company_id
            )
        )
        assert preference is not None
        preference.services = ["External audit", "Donor programme assurance"]
        db.commit()
    second = match_verified_opportunity(opportunity_id, matcher=matcher)[0]

    assert second.status is CompanyMatchProcessingStatus.MATCHED
    assert second.match is not None
    assert second.match.score == 90
    assert len(matcher.calls) == 2


def test_opportunity_analysis_change_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(isolated_session_factory)
    company_id = company_ids[0]
    matcher = RecordingMatcher(
        {
            company_id: [
                _match(company_id, score=82, strength="STRONG"),
                _match(company_id, score=71, strength="STRONG"),
            ]
        }
    )

    match_verified_opportunity(opportunity_id, matcher=matcher)
    changed_analysis = _analysis(
        scope="Assess controls and deliver in-person training.",
        deliverables=["Assessment report", "Training workshop"],
    )
    with isolated_session_factory() as db:
        record = db.scalar(
            select(AIOpportunityAnalysis).where(
                AIOpportunityAnalysis.opportunity_id == opportunity_id
            )
        )
        assert record is not None
        record.analysis_data = changed_analysis.model_dump(mode="json")
        db.commit()
    second = match_verified_opportunity(opportunity_id, matcher=matcher)[0]

    assert second.status is CompanyMatchProcessingStatus.MATCHED
    assert len(matcher.calls) == 2


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (GeminiAPIError("provider details"), "Gemini company matching failed."),
        ({"score": 90}, "Gemini returned an invalid company match."),
    ],
    ids=["api-failure", "malformed-response"],
)
def test_match_failure_is_persisted_but_not_cached(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
    response: object,
    expected_error: str,
) -> None:
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(isolated_session_factory)
    company_id = company_ids[0]
    matcher = RecordingMatcher(
        {
            company_id: [
                response,
                _match(company_id, score=84, strength="STRONG"),
            ]
        }
    )

    outcome = match_verified_opportunity(opportunity_id, matcher=matcher)[0]

    assert outcome.status is CompanyMatchProcessingStatus.ERROR
    assert outcome.qualifies_for_feed is False
    with isolated_session_factory() as db:
        record = db.scalar(select(CompanyOpportunityMatch))
        assert record is not None
        assert record.error_reason == expected_error
        assert record.score is None

    retried = match_verified_opportunity(opportunity_id, matcher=matcher)[0]
    assert retried.status is CompanyMatchProcessingStatus.MATCHED
    assert len(matcher.calls) == 2


@pytest.mark.parametrize("field", list(CompanyIntelligence.model_fields) + [
    "services", "industries", "countries", "regions", "opportunity_types",
    "keywords", "languages", "minimum_match_score",
])
def test_rich_profile_changes_rematch_only_changed_company(
    monkeypatch, isolated_session_factory, field,
):
    from app.intelligence import opportunity_pipeline

    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, ids = _seed(isolated_session_factory, companies=[
        {"name": "Evaluation Company"}, {"name": "Engineering Company"},
    ])
    matcher = RecordingMatcher({
        ids[0]: [_match(ids[0], score=88, strength="STRONG"),
                 _match(ids[0], score=64, strength="MODERATE")],
        ids[1]: [_match(ids[1], score=32, strength="WEAK")],
    })

    def forbidden(**_kwargs):
        raise AssertionError("A profile edit must not reanalyze an opportunity")

    monkeypatch.setattr(opportunity_pipeline, "analyze_opportunity", forbidden)
    match_verified_opportunity(opportunity_id, matcher=matcher)
    cached = match_verified_opportunity(opportunity_id, matcher=matcher)
    assert all(item.status is CompanyMatchProcessingStatus.CACHED for item in cached)
    assert len(matcher.calls) == 2
    with isolated_session_factory() as db:
        record = db.scalar(select(OpportunityPreference).where(
            OpportunityPreference.organization_id == ids[0]
        ))
        analysis_before = db.scalar(select(AIOpportunityAnalysis)).analysis_data
        if field in CompanyIntelligence.model_fields:
            record.intelligence = {field: ["New documented profile fact"]}
        elif field == "minimum_match_score":
            record.minimum_match_score = 90
        else:
            setattr(record, field, ["New documented profile fact"])
        db.commit()

    outcomes = match_verified_opportunity(opportunity_id, matcher=matcher)
    assert outcomes[0].status is CompanyMatchProcessingStatus.MATCHED
    assert outcomes[1].status is CompanyMatchProcessingStatus.CACHED
    assert len(matcher.calls) == 3
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(CompanyOpportunityMatch)) == 2
        assert db.scalar(select(AIOpportunityAnalysis)).analysis_data == analysis_before


def test_rich_profile_normalization_and_minimal_profile(
    isolated_session_factory,
):
    _, ids = _seed(isolated_session_factory)
    with isolated_session_factory() as db:
        organization = db.get(Organization, ids[0])
        minimal = matching_engine.build_company_profile(organization, None).profile
        assert minimal["capabilities"] == []
        assert all(not values for values in minimal["experience"].values())
        assert all(not values for values in minimal["exclusions"].values())
        preference = db.scalar(select(OpportunityPreference))
        preference.intelligence = {
            "capabilities": [" Programme evaluation ", "programme evaluation", ""],
            "areas_of_expertise": ["Agricultural economics"],
            "assignment_types": ["Impact evaluation"],
            "client_types": ["Development partners"],
            "sector_experience": ["Agriculture"],
            "relevant_experience": ["Five evaluations in Rwanda"],
            "track_record": ["Programme review, delivered in 2025"],
            "preferred_sectors": ["Rural development"],
            "excluded_services": ["Construction supervision"],
            "excluded_sectors": ["Mining"],
            "excluded_countries": ["Unsupported market"],
            "excluded_opportunity_types": ["Goods supply"],
        }
        profile = matching_engine.build_company_profile(organization, preference).profile
        assert profile["capabilities"] == ["Programme evaluation"]
        assert profile["experience"]["track_record"] == [
            "Programme review, delivered in 2025"
        ]
        assert profile["preferred_sectors"] == ["Rural development"]
        assert profile["exclusions"]["services"] == ["Construction supervision"]
        assert profile["exclusions"]["countries"] == ["Unsupported market"]
        assert "intelligence" not in profile
        assert "organization_id" not in profile
