from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from app import scanner
from app.intelligence import matching_engine, opportunity_pipeline
from app.intelligence import triage_engine
from app.intelligence.schemas import CompanyMatch, OpportunityAnalysis
from app.models import (
    AIOpportunityAnalysis,
    CompanyOpportunityMatch,
    Opportunity,
    OpportunityPreference,
    Organization,
)
from app.schemas import RawOpportunity


class StubScanner:
    source_name = "Phase 3 test source"

    def __init__(self, opportunities: list[RawOpportunity]) -> None:
        self.opportunities = opportunities

    def scan(self) -> list[RawOpportunity]:
        return self.opportunities


def _analysis() -> OpportunityAnalysis:
    return OpportunityAnalysis.model_validate(
        {
            "decision": "ACCEPT",
            "confidence": 96,
            "reason": "A specific active consulting procurement notice.",
            "title": "Independent review of programme financial controls",
            "buyer_name": "Example Development Programme",
            "issuing_organisation": "Example Development Programme",
            "country": "Rwanda",
            "location": "Kigali",
            "opportunity_type": "PROFESSIONAL_SERVICE",
            "status": "ACTIVE",
            "deadline": "2099-12-31",
            "summary": "Review programme financial and internal controls.",
            "scope": "Assess controls and provide an independent report.",
            "services_required": ["Financial controls review"],
            "eligibility_requirements": ["Registered professional firm"],
            "experience_requirements": ["Three similar assignments"],
            "deliverables": ["Independent controls report"],
            "original_source_url": "https://source-one.test/notices/review",
            "evidence": ["Qualified firms are invited to submit proposals."],
        }
    )


def _raw(source_url: str) -> RawOpportunity:
    return RawOpportunity(
        organisation_name="Example Development Programme",
        title="Independent review of programme financial controls",
        source_name="Example procurement portal",
        source_url=source_url,
        description="Assess controls and provide an independent report.",
        deadline=date(2099, 12, 31),
        country_context="Rwanda",
    )


def _company_match(
    company_id: int,
    score: int,
    strength: str,
) -> CompanyMatch:
    return CompanyMatch(
        company_id=company_id,
        score=score,
        match_strength=strength,
        explanation=f"Stored semantic explanation for company {company_id}.",
        strengths=["Relevant service capability"],
        potential_gaps=["Confirm similar assignment evidence"],
        eligibility_concerns=["Confirm current registration"],
        recommendation="Review the notice and confirm eligibility.",
    )


def _seed_companies(isolated_session_factory) -> list[int]:
    company_ids: list[int] = []
    with isolated_session_factory() as db:
        for name, services in (
            ("Alpha Advisory", ["Financial controls review"]),
            ("Beta Research", ["Market research"]),
        ):
            organization = Organization(
                name=name,
                country="Rwanda",
                description=f"Profile for {name}",
                is_active=True,
            )
            db.add(organization)
            db.flush()
            db.add(
                OpportunityPreference(
                    organization_id=organization.id,
                    countries=["Rwanda"],
                    regions=[],
                    industries=["Professional services"],
                    services=services,
                    opportunity_types=["Professional services"],
                    keywords=[],
                    languages=["English"],
                    minimum_match_score=70,
                )
            )
            company_ids.append(organization.id)
        db.commit()
    return company_ids


def _configure_scan(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
    raw_opportunities: list[RawOpportunity],
) -> None:
    def unavailable_triage(**_kwargs):
        raise RuntimeError("Triage is mocked independently in Phase 7 tests")

    monkeypatch.setattr(triage_engine, "analyze_triage", unavailable_triage)
    monkeypatch.setattr(scanner, "SessionLocal", isolated_session_factory)
    monkeypatch.setattr(
        opportunity_pipeline,
        "SessionLocal",
        isolated_session_factory,
    )
    monkeypatch.setattr(
        matching_engine,
        "SessionLocal",
        isolated_session_factory,
    )
    monkeypatch.setattr(scanner, "approve_existing_sources", lambda: 0)
    monkeypatch.setattr(scanner, "mark_expired_opportunities", lambda: 0)
    monkeypatch.setattr(
        scanner,
        "get_scanners",
        lambda: [StubScanner(raw_opportunities)],
    )

    def process_candidate(
        raw: RawOpportunity,
        *,
        country_context: str | None = None,
    ):
        return opportunity_pipeline.process_opportunity_candidate(
            raw,
            country_context=country_context,
            analyzer=lambda **_kwargs: _analysis(),
        )

    monkeypatch.setattr(
        scanner,
        "process_opportunity_candidate",
        process_candidate,
    )


def test_verified_opportunity_is_matched_to_multiple_companies(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    company_ids = _seed_companies(isolated_session_factory)
    _configure_scan(
        monkeypatch,
        isolated_session_factory,
        [_raw("https://source-one.test/notices/review")],
    )
    calls: list[int] = []

    def matcher(**kwargs: object) -> CompanyMatch:
        company_id = int(str(kwargs["company_id"]))
        calls.append(company_id)
        score = 93 if company_id == company_ids[0] else 41
        strength = "VERY_STRONG" if score > 70 else "WEAK"
        return _company_match(company_id, score, strength)

    monkeypatch.setattr(
        matching_engine,
        "match_company_to_opportunity",
        matcher,
    )

    scanner.run_scanner()

    assert calls == company_ids
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 1
        matches = list(
            db.scalars(
                select(CompanyOpportunityMatch).order_by(
                    CompanyOpportunityMatch.organization_id
                )
            ).all()
        )
    assert [match.score for match in matches] == [93, 41]
    assert [match.qualifies_for_feed for match in matches] == [True, False]


def test_verified_opportunity_is_automatically_matched_to_one_active_company(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    company_ids = _seed_companies(isolated_session_factory)
    with isolated_session_factory() as db:
        inactive = db.get(Organization, company_ids[1])
        assert inactive is not None
        inactive.is_active = False
        db.commit()
    _configure_scan(
        monkeypatch,
        isolated_session_factory,
        [_raw("https://source-one.test/notices/review")],
    )
    calls: list[int] = []

    def matcher(**kwargs: object) -> CompanyMatch:
        company_id = int(str(kwargs["company_id"]))
        calls.append(company_id)
        return _company_match(company_id, 91, "VERY_STRONG")

    monkeypatch.setattr(
        matching_engine,
        "match_company_to_opportunity",
        matcher,
    )

    scanner.run_scanner()

    assert calls == [company_ids[0]]
    with isolated_session_factory() as db:
        match = db.scalar(select(CompanyOpportunityMatch))
    assert match is not None
    assert match.organization_id == company_ids[0]
    assert match.qualifies_for_feed is True


def test_one_company_failure_does_not_stop_other_matching(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    company_ids = _seed_companies(isolated_session_factory)
    _configure_scan(
        monkeypatch,
        isolated_session_factory,
        [_raw("https://source-one.test/notices/review")],
    )

    def matcher(**kwargs: object) -> CompanyMatch:
        company_id = int(str(kwargs["company_id"]))
        if company_id == company_ids[0]:
            raise RuntimeError("mocked provider failure")
        return _company_match(company_id, 88, "STRONG")

    monkeypatch.setattr(
        matching_engine,
        "match_company_to_opportunity",
        matcher,
    )

    scanner.run_scanner()

    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 1
        matches = list(
            db.scalars(
                select(CompanyOpportunityMatch).order_by(
                    CompanyOpportunityMatch.organization_id
                )
            ).all()
        )
    assert matches[0].error_reason == "Gemini company matching failed."
    assert matches[1].score == 88
    assert matches[1].qualifies_for_feed is True


def test_duplicate_source_preserves_provenance_without_duplicate_opportunity(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
) -> None:
    company_ids = _seed_companies(isolated_session_factory)
    _configure_scan(
        monkeypatch,
        isolated_session_factory,
        [
            _raw("https://source-one.test/notices/review"),
            _raw("https://source-two.test/notices/review-copy"),
        ],
    )
    calls: list[int] = []

    def matcher(**kwargs: object) -> CompanyMatch:
        company_id = int(str(kwargs["company_id"]))
        calls.append(company_id)
        return _company_match(company_id, 86, "STRONG")

    monkeypatch.setattr(
        matching_engine,
        "match_company_to_opportunity",
        matcher,
    )

    scanner.run_scanner()

    with isolated_session_factory() as db:
        opportunity_count = db.scalar(
            select(func.count()).select_from(Opportunity)
        )
        provenance = list(
            db.scalars(
                select(AIOpportunityAnalysis).order_by(AIOpportunityAnalysis.id)
            ).all()
        )
    assert opportunity_count == 1
    assert len(provenance) == 2
    assert provenance[0].opportunity_id == provenance[1].opportunity_id
    assert calls == company_ids


def _seed_stored_feed(isolated_session_factory) -> tuple[int, int, int, int]:
    from conftest import stamp_current_fixture_matches

    now = datetime.now(timezone.utc)
    with isolated_session_factory() as db:
        alpha = Organization(name="Alpha Advisory", is_active=True)
        beta = Organization(name="Beta Research", is_active=True)
        db.add_all((alpha, beta))
        db.flush()
        alpha_opportunity = Opportunity(
            title="Alpha controls assignment",
            category="PROFESSIONAL_SERVICE",
            source_name="Portal",
            source_url="https://feed.test/alpha",
            description="Controls review.",
            status="New",
            is_expired=False,
            first_discovered_at=now,
            last_seen_at=now,
        )
        beta_opportunity = Opportunity(
            title="Beta research assignment",
            category="CONSULTANCY",
            source_name="Portal",
            source_url="https://feed.test/beta",
            description="Research assignment.",
            status="New",
            is_expired=False,
            first_discovered_at=now,
            last_seen_at=now,
        )
        db.add_all((alpha_opportunity, beta_opportunity))
        db.flush()

        pairs = (
            (alpha.id, alpha_opportunity.id, 94, "VERY_STRONG", True),
            (beta.id, alpha_opportunity.id, 35, "WEAK", False),
            (alpha.id, beta_opportunity.id, 44, "WEAK", False),
            (beta.id, beta_opportunity.id, 87, "STRONG", True),
        )
        for index, (company_id, opportunity_id, score, strength, qualifies) in (
            enumerate(pairs)
        ):
            db.add(
                CompanyOpportunityMatch(
                    organization_id=company_id,
                    opportunity_id=opportunity_id,
                    score=score,
                    match_strength=strength,
                    explanation=f"Stored explanation {index}",
                    strengths=["Stored strength"],
                    potential_gaps=["Stored gap"],
                    eligibility_concerns=["Stored concern"],
                    recommendation="Stored recommendation",
                    minimum_score=70,
                    qualifies_for_feed=qualifies,
                    match_fingerprint=f"{index:064d}",
                    matched_at=now,
                )
            )
        stamp_current_fixture_matches(db)
        db.commit()
        return alpha.id, beta.id, alpha_opportunity.id, beta_opportunity.id


def test_discover_all_and_individual_company_views_use_stored_matches_only(
    client,
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    alpha_id, beta_id, _, _ = _seed_stored_feed(isolated_session_factory)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Rendering must not call an AI or matching engine.")

    monkeypatch.setattr(main, "score_for_business", forbidden)
    monkeypatch.setattr(
        matching_engine,
        "match_company_to_opportunity",
        forbidden,
    )
    monkeypatch.setattr(opportunity_pipeline, "analyze_opportunity", forbidden)

    all_companies = client.get("/")
    alpha = client.get(f"/?company_id={alpha_id}")
    beta = client.get(f"/?company_id={beta_id}")

    assert all_companies.status_code == 200
    assert "Alpha controls assignment" in all_companies.text
    assert "Beta research assignment" in all_companies.text
    assert "Alpha controls assignment" in alpha.text
    assert "Beta research assignment" not in alpha.text
    assert "Beta research assignment" in beta.text
    assert "Alpha controls assignment" not in beta.text
    assert "Stored gap" in alpha.text
    assert "Stored concern" in alpha.text
    assert "Very Strong" in alpha.text


def test_detail_uses_selected_stored_match_without_ai_calls(
    client,
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    alpha_id, _, alpha_opportunity_id, _ = _seed_stored_feed(
        isolated_session_factory
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Detail rendering must not call AI.")

    monkeypatch.setattr(main, "score_for_business", forbidden)
    monkeypatch.setattr(
        matching_engine,
        "match_company_to_opportunity",
        forbidden,
    )
    monkeypatch.setattr(opportunity_pipeline, "analyze_opportunity", forbidden)

    response = client.get(
        f"/opportunities/{alpha_opportunity_id}?company_id={alpha_id}"
    )

    assert response.status_code == 200
    assert "Alpha Advisory" in response.text
    assert "94" in response.text
    assert "Stored explanation 0" in response.text
    assert "Stored gap" in response.text
    assert "Stored concern" in response.text
