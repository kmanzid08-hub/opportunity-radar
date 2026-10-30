from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock

import pytest
from sqlalchemy import event, func, select

from app.intelligence import company_documents, readiness_engine
from app.intelligence.company_documents import CompanyDocumentInventory
from app.intelligence.gemini_client import GeminiAPIError, GeminiClient
from app.intelligence.readiness_analyzer import analyze_readiness, extract_requirements
from app.intelligence.readiness_engine import analyze_opportunity_readiness
from app.intelligence.readiness_schemas import (
    ReadinessAnalysis,
    RequirementExtraction,
    RequirementStatus,
    requirement_key,
)
from app.models import (
    AIOpportunityAnalysis,
    CompanyOpportunityReadiness,
    Opportunity,
    OpportunityPreference,
    OpportunityRequirement,
    OpportunityRequirementExtraction,
)

from .test_matching_engine import _analysis, _configure, _seed


TODAY = datetime.now(timezone.utc).date()


def analysis(**overrides):
    return _analysis(
        **{
            "required_documents": ["Valid tax clearance certificate"],
            "experience_requirements": ["Three similar assignments within five years"],
            "eligibility_requirements": [],
            "qualification_requirements": [],
            "deadline": TODAY + timedelta(days=30),
            **overrides,
        }
    )


def extraction(opportunity):
    items = []
    for field, category in (
        ("required_documents", "TAX"),
        ("experience_requirements", "EXPERIENCE"),
        ("eligibility_requirements", "LEGAL_REGISTRATION"),
        ("qualification_requirements", "PROFESSIONAL_CREDENTIAL"),
    ):
        for value in getattr(opportunity, field):
            items.append(
                dict(
                    category=category,
                    requirement=value,
                    source_field=field,
                    evidence=value,
                    required=True,
                    critical=field == "eligibility_requirements",
                    requires_document=field == "required_documents",
                    confidence=96,
                )
            )
    return RequirementExtraction.model_validate({"requirements": items})


def assessment(
    requirements, *, document="UNKNOWN", experience="KNOWN_AVAILABLE", document_ids=None
):
    statuses = {
        "UNKNOWN": "NEEDS_CONFIRMATION",
        "KNOWN_AVAILABLE": "READY",
        "KNOWN_MISSING": "MISSING",
    }
    items = []
    for requirement in requirements.requirements:
        available = document if requirement.requires_document else experience
        items.append(
            dict(
                requirement_id=requirement_key(requirement),
                status=statuses[available],
                availability=available,
                confidence=90,
                notes="Only two qualifying assignments are documented; three are required."
                if available == "KNOWN_MISSING" and not requirement.requires_document
                else "Assessment based on supplied records; unavailable facts remain unknown.",
                next_action="Confirm supporting evidence before submission."
                if available != "KNOWN_AVAILABLE"
                else "",
                document_ids=(document_ids or [])
                if requirement.requires_document
                else [],
            )
        )
    return ReadinessAnalysis.model_validate({"items": items})


@pytest.fixture
def setup_readiness(monkeypatch, isolated_session_factory):
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, companies = _seed(isolated_session_factory, analysis=analysis())
    with isolated_session_factory() as db:
        db.scalar(select(OpportunityPreference)).intelligence = {
            "track_record": [
                "Similar assignment A, 2024",
                "Similar assignment B, 2025",
                "Similar assignment C, 2026",
            ],
        }
        db.commit()
    extract = Mock(side_effect=lambda **kw: extraction(kw["opportunity"]))
    assess = Mock(side_effect=lambda **kw: assessment(kw["requirements"]))
    monkeypatch.setattr(readiness_engine, "extract_requirements", extract)
    monkeypatch.setattr(readiness_engine, "analyze_readiness", assess)
    return opportunity_id, companies[0], extract, assess


def inventory(monkeypatch, company_id, *, complete=False, documents=None):
    snapshot = CompanyDocumentInventory.model_validate(
        dict(
            company_id=company_id,
            complete=complete,
            documents=documents or [],
        )
    )
    monkeypatch.setattr(
        company_documents, "load_company_documents", lambda ids: {company_id: snapshot}
    )
    return snapshot


def test_requirements_are_persisted_with_evidence_and_company_identity(
    setup_readiness, isolated_session_factory
):
    opportunity_id, company_id, _, _ = setup_readiness
    outcome = analyze_opportunity_readiness(opportunity_id)[0]
    assert outcome.status == "ANALYZED"
    assert outcome.summary.ready_count == 1
    assert outcome.summary.confirmation_count == 1
    assert outcome.summary.overall_status.value == "NEEDS_WORK"
    assert outcome.summary.readiness_score == 50
    with isolated_session_factory() as db:
        rows = db.scalars(
            select(OpportunityRequirement).order_by(OpportunityRequirement.id)
        ).all()
        assert len(rows) == 2
        assert {row.company_id for row in rows} == {company_id}
        assert {row.opportunity_id for row in rows} == {opportunity_id}
        assert (
            rows[0].requirement == rows[0].evidence == "Valid tax clearance certificate"
        )
        assert rows[0].status == "NEEDS_CONFIRMATION"
        assert rows[0].required is True
        assert rows[0].source_url == analysis().original_source_url
        assert db.scalar(select(CompanyOpportunityReadiness)).error_reason is None


@pytest.mark.parametrize("change", ["invent", "omit", "rewrite", "duplicate"])
def test_unsupported_or_incomplete_requirements_are_rejected(
    setup_readiness, isolated_session_factory, change
):
    opportunity_id, _, extract, assess = setup_readiness
    payload = extraction(analysis()).model_dump(mode="json")
    if change == "invent":
        payload["requirements"][0].update(
            requirement="Invented bank guarantee", evidence="Invented bank guarantee"
        )
    elif change == "omit":
        payload["requirements"].pop()
    elif change == "rewrite":
        payload["requirements"][0]["requirement"] = (
            "Tax clearance plus invented local registration"
        )
    else:
        payload["requirements"].append(payload["requirements"][0])
    extract.side_effect = None
    extract.return_value = RequirementExtraction.model_validate(payload)
    assert analyze_opportunity_readiness(opportunity_id)[0].status == "ERROR"
    assess.assert_not_called()
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(OpportunityRequirement)) == 0
        assert db.scalar(select(OpportunityRequirementExtraction)).error_reason


@pytest.mark.parametrize(
    ("complete", "records", "state", "expected"),
    [
        (False, [], "UNKNOWN", "NEEDS_CONFIRMATION"),
        (True, [], "KNOWN_MISSING", "MISSING"),
        (
            False,
            [
                {
                    "record_id": "tax-1",
                    "label": "Tax clearance",
                    "availability": "KNOWN_AVAILABLE",
                }
            ],
            "KNOWN_AVAILABLE",
            "READY",
        ),
        (
            False,
            [
                {
                    "record_id": "tax-1",
                    "label": "Tax clearance",
                    "availability": "KNOWN_MISSING",
                }
            ],
            "KNOWN_MISSING",
            "MISSING",
        ),
    ],
)
def test_document_available_missing_or_unknown(
    setup_readiness,
    monkeypatch,
    isolated_session_factory,
    complete,
    records,
    state,
    expected,
):
    opportunity_id, company_id, _, assess = setup_readiness
    inventory(monkeypatch, company_id, complete=complete, documents=records)
    assess.side_effect = lambda **kw: assessment(
        kw["requirements"], document=state, document_ids=["tax-1"] if records else []
    )
    assert analyze_opportunity_readiness(opportunity_id)[0].status == "ANALYZED"
    with isolated_session_factory() as db:
        row = db.scalar(
            select(OpportunityRequirement).where(
                OpportunityRequirement.requires_document.is_(True)
            )
        )
        assert row.status == expected
        assert row.availability == state


def test_profile_registration_does_not_prove_a_document_exists(
    setup_readiness, isolated_session_factory
):
    opportunity_id, _, _, assess = setup_readiness
    assess.side_effect = lambda **kw: assessment(
        kw["requirements"], document="KNOWN_AVAILABLE"
    )
    assert analyze_opportunity_readiness(opportunity_id)[0].status == "ERROR"
    with isolated_session_factory() as db:
        assert db.scalar(select(CompanyOpportunityReadiness)).result_data is None


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("KNOWN_AVAILABLE", "READY"),
        ("KNOWN_MISSING", "MISSING"),
        ("UNKNOWN", "NEEDS_CONFIRMATION"),
    ],
)
def test_experience_satisfied_partial_or_unknown(
    setup_readiness, isolated_session_factory, state, expected
):
    opportunity_id, _, _, assess = setup_readiness
    with isolated_session_factory() as db:
        count = {"KNOWN_AVAILABLE": 3, "KNOWN_MISSING": 2, "UNKNOWN": 0}[state]
        db.scalar(select(OpportunityPreference)).intelligence = {
            "track_record": [
                f"Similar assignment {number}, 2025" for number in range(count)
            ],
        }
        db.commit()
    assess.side_effect = lambda **kw: assessment(kw["requirements"], experience=state)
    analyze_opportunity_readiness(opportunity_id)
    assert (
        len(assess.call_args.kwargs["company_profile"]["experience"]["track_record"])
        == count
    )
    with isolated_session_factory() as db:
        row = db.scalar(
            select(OpportunityRequirement).where(
                OpportunityRequirement.category == "EXPERIENCE"
            )
        )
        assert row.status == expected
        if expected == "MISSING":
            assert "two qualifying" in row.notes


def test_eligibility_gap_overrides_readiness_indicator(
    setup_readiness, isolated_session_factory
):
    opportunity_id, _, _, assess = setup_readiness
    with isolated_session_factory() as db:
        record = db.scalar(select(AIOpportunityAnalysis))
        record.analysis_data = analysis(
            eligibility_requirements=["Professional licence required"]
        ).model_dump(mode="json")
        db.commit()
    assess.side_effect = lambda **kw: assessment(
        kw["requirements"], experience="KNOWN_MISSING"
    )
    outcome = analyze_opportunity_readiness(opportunity_id)[0]
    assert outcome.summary.critical_gap_count == 1
    assert outcome.summary.overall_status.value == "SIGNIFICANT_GAPS"


def test_unchanged_inputs_reuse_both_caches_and_item_ids(
    setup_readiness, isolated_session_factory
):
    opportunity_id, _, extract, assess = setup_readiness
    analyze_opportunity_readiness(opportunity_id)
    with isolated_session_factory() as db:
        ids = list(db.scalars(select(OpportunityRequirement.id)))
    assert analyze_opportunity_readiness(opportunity_id)[0].status == "CACHED"
    extract.assert_called_once()
    assess.assert_called_once()
    with isolated_session_factory() as db:
        assert list(db.scalars(select(OpportunityRequirement.id))) == ids


@pytest.mark.parametrize(
    "change", ["profile", "document_upload", "document_remove", "requirements"]
)
def test_readiness_cache_invalidation_is_scoped(
    setup_readiness, isolated_session_factory, monkeypatch, change
):
    opportunity_id, company_id, extract, assess = setup_readiness
    initial = [
        {"record_id": "tax-1", "label": "Tax clearance", "availability": "UNKNOWN"}
    ]
    inventory(
        monkeypatch,
        company_id,
        documents=initial if change == "document_remove" else [],
    )
    analyze_opportunity_readiness(opportunity_id)
    with isolated_session_factory() as db:
        if change == "profile":
            db.scalar(select(OpportunityPreference)).intelligence = {
                "track_record": ["Additional assignment"]
            }
        elif change == "requirements":
            record = db.scalar(select(AIOpportunityAnalysis))
            record.analysis_data = analysis(
                experience_requirements=["Four similar assignments"]
            ).model_dump(mode="json")
        db.commit()
    if change in {"document_upload", "document_remove"}:
        inventory(
            monkeypatch,
            company_id,
            documents=initial if change == "document_upload" else [],
        )
    from app.intelligence import opportunity_pipeline, matching_engine, source_analyzer

    forbidden = Mock(
        side_effect=AssertionError("Upstream AI is not needed for readiness")
    )
    monkeypatch.setattr(opportunity_pipeline, "analyze_opportunity", forbidden)
    monkeypatch.setattr(matching_engine, "match_company_to_opportunity", forbidden)
    monkeypatch.setattr(source_analyzer, "analyze_source", forbidden)
    assert analyze_opportunity_readiness(opportunity_id)[0].status == "ANALYZED"
    assert extract.call_count == (2 if change == "requirements" else 1)
    assert assess.call_count == 2
    forbidden.assert_not_called()
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(OpportunityRequirement)) == 2


@pytest.mark.parametrize("stage", ["extract", "assess"])
@pytest.mark.parametrize(
    "failure", [GeminiAPIError("credential-like provider details"), {"invalid": True}]
)
def test_gemini_failure_or_malformed_response_is_recorded_without_fake_data(
    setup_readiness,
    isolated_session_factory,
    stage,
    failure,
):
    opportunity_id, _, extract, assess = setup_readiness
    target = extract if stage == "extract" else assess
    target.side_effect = [failure]
    assert analyze_opportunity_readiness(opportunity_id)[0].status == "ERROR"
    with isolated_session_factory() as db:
        saved = db.scalar(select(CompanyOpportunityReadiness))
        assert saved.result_data is None
        assert "credential" not in saved.error_reason
        assert db.get(Opportunity, opportunity_id) is not None
        assert db.scalar(select(func.count()).select_from(OpportunityRequirement)) == 0


def test_failed_result_can_be_explicitly_retried(setup_readiness):
    opportunity_id, _, extract, assess = setup_readiness
    assess.side_effect = [GeminiAPIError("failure"), assessment(extraction(analysis()))]
    assert analyze_opportunity_readiness(opportunity_id)[0].status == "ERROR"
    assert analyze_opportunity_readiness(opportunity_id)[0].status == "ANALYZED"
    extract.assert_called_once()
    assert assess.call_count == 2


def test_multiple_companies_have_independent_readiness_and_shared_extraction(
    monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, company_ids = _seed(
        isolated_session_factory,
        analysis=analysis(),
        companies=[
            {"name": "Experienced Company"},
            {"name": "New Company"},
            {"name": "Failing Company"},
        ],
    )
    extract = Mock(return_value=extraction(analysis()))
    assess = Mock(
        side_effect=[
            assessment(extraction(analysis()), experience="KNOWN_AVAILABLE"),
            assessment(extraction(analysis()), experience="KNOWN_MISSING"),
            GeminiAPIError("failure"),
        ]
    )
    results = analyze_opportunity_readiness(
        opportunity_id, extractor=extract, analyzer=assess
    )
    assert [item.company_id for item in results] == company_ids
    assert [item.status for item in results] == ["ANALYZED", "ANALYZED", "ERROR"]
    assert results[0].summary.readiness_score != results[1].summary.readiness_score
    extract.assert_called_once()
    with isolated_session_factory() as db:
        assert (
            db.scalar(select(func.count()).select_from(CompanyOpportunityReadiness))
            == 3
        )
        assert db.scalar(select(func.count()).select_from(OpportunityRequirement)) == 4


def test_empty_requirements_are_unknown_not_ready(
    setup_readiness, isolated_session_factory
):
    opportunity_id, _, _, assess = setup_readiness
    with isolated_session_factory() as db:
        db.scalar(select(AIOpportunityAnalysis)).analysis_data = analysis(
            required_documents=[], experience_requirements=[]
        ).model_dump(mode="json")
        db.commit()
    result = analyze_opportunity_readiness(opportunity_id)[0]
    assert result.summary.readiness_score is None
    assert result.summary.overall_status.value == "UNKNOWN"
    assess.assert_not_called()


def test_prompt_uses_metadata_only_and_complete_company_evidence():
    client = Mock(spec=GeminiClient)
    definitions = extraction(analysis())
    client.generate_structured.return_value = definitions
    assert extract_requirements(opportunity=analysis(), client=client) == definitions
    assert (
        "Do not infer standard tender requirements"
        in client.generate_structured.call_args.kwargs["system_instruction"]
    )
    profile = {
        "services": ["Assurance"],
        "experience": {"track_record": ["Two assignments"]},
    }
    documents = CompanyDocumentInventory(company_id=1)
    client.generate_structured.return_value = assessment(definitions)
    analyze_readiness(
        opportunity=analysis(),
        requirements=definitions,
        company_profile=profile,
        documents=documents,
        client=client,
    )
    payload = json.loads(client.generate_structured.call_args.args[0])
    assert payload["company_profile"] == profile
    assert payload["documents"] == {"company_id": 1, "complete": False, "documents": []}
    assert len(payload["requirements"]) == 2
    instruction = client.generate_structured.call_args.kwargs["system_instruction"]
    assert "METADATA ONLY" in instruction
    assert "Generic claims of experience require confirmation" in instruction
    with pytest.raises(ValueError):
        CompanyDocumentInventory.model_validate(
            {"company_id": 1, "private_contents": "must never be included"}
        )


def test_explicit_action_creates_readiness_and_get_pages_make_no_ai_calls(
    setup_readiness, client, monkeypatch, isolated_session_factory
):
    opportunity_id, company_id, extract, assess = setup_readiness
    response = client.post(
        f"/opportunities/{opportunity_id}/readiness/analyze",
        data={"company_id": company_id},
    )
    assert response.status_code == 200
    assert "Approximately 50%" in response.text
    assert "Preparation checklist" in response.text
    with isolated_session_factory() as db:
        db.get(Opportunity, opportunity_id).is_lead = True
        db.commit()
    forbidden = Mock(side_effect=AssertionError("No AI in rendering"))
    monkeypatch.setattr(GeminiClient, "generate_structured", forbidden)
    monkeypatch.setattr(GeminiClient, "generate_text", forbidden)
    for _ in range(2):
        detail = client.get(f"/opportunities/{opportunity_id}?company_id={company_id}")
        pipeline = client.get("/pipeline")
        assert detail.status_code == pipeline.status_code == 200
        assert "Valid tax clearance certificate" in detail.text
        assert "1 items outstanding" in pipeline.text
        assert "Valid tax clearance certificate" not in pipeline.text
    extract.assert_called_once()
    assess.assert_called_once()
    forbidden.assert_not_called()


@pytest.mark.parametrize("change", ["profile", "requirements", "documents"])
def test_changed_inputs_hide_stale_readiness_without_ai(
    setup_readiness, client, monkeypatch, isolated_session_factory, change
):
    opportunity_id, company_id, _, assess = setup_readiness
    analyze_opportunity_readiness(opportunity_id)
    with isolated_session_factory() as db:
        if change == "profile":
            db.scalar(select(OpportunityPreference)).intelligence = {
                "capabilities": ["Updated information"]
            }
        elif change == "requirements":
            db.scalar(select(AIOpportunityAnalysis)).analysis_data = analysis(
                required_documents=["Different document"]
            ).model_dump(mode="json")
        db.commit()
    if change == "documents":
        inventory(monkeypatch, company_id, complete=True)
    page = client.get(f"/opportunities/{opportunity_id}?company_id={company_id}")
    assert "Readiness needs refresh" in page.text
    assert "Approximately 50%" not in page.text
    assess.assert_called_once()


def test_invalid_company_and_nonverified_opportunity_do_not_call_ai(
    setup_readiness, client, isolated_session_factory
):
    opportunity_id, company_id, extract, assess = setup_readiness
    assert (
        client.post(
            f"/opportunities/{opportunity_id}/readiness/analyze",
            data={"company_id": 99999},
        ).status_code
        == 404
    )
    with isolated_session_factory() as db:
        db.scalar(select(AIOpportunityAnalysis)).analysis_data = analysis(
            decision="REJECT"
        ).model_dump(mode="json")
        db.commit()
    assert (
        client.post(
            f"/opportunities/{opportunity_id}/readiness/analyze",
            data={"company_id": company_id},
        ).status_code
        == 400
    )
    extract.assert_not_called()
    assess.assert_not_called()


def test_pipeline_readiness_is_batched(
    setup_readiness, client, isolated_session_factory
):
    opportunity_id, _, _, _ = setup_readiness
    analyze_opportunity_readiness(opportunity_id)
    with isolated_session_factory() as db:
        db.get(Opportunity, opportunity_id).is_lead = True
        db.commit()
    engine = isolated_session_factory.kw["bind"]
    selects = []

    def count(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        assert client.get("/pipeline").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(selects) == 3


def test_expired_document_is_unusable(setup_readiness, monkeypatch):
    opportunity_id, company_id, _, assess = setup_readiness
    inventory(
        monkeypatch,
        company_id,
        documents=[
            {
                "record_id": "tax-1",
                "label": "Tax clearance",
                "availability": "KNOWN_AVAILABLE",
                "valid_until": TODAY - timedelta(days=1),
            }
        ],
    )
    assess.side_effect = lambda **kw: assessment(
        kw["requirements"], document="KNOWN_MISSING", document_ids=["tax-1"]
    )
    result = analyze_opportunity_readiness(opportunity_id)[0]
    assert result.summary.missing_count == 1
    assert (
        assess.call_args.kwargs["documents"].documents[0].availability.value
        == "KNOWN_MISSING"
    )


def test_document_inventory_cannot_leak_across_companies(setup_readiness, monkeypatch):
    opportunity_id, company_id, _, assess = setup_readiness
    monkeypatch.setattr(
        company_documents,
        "load_company_documents",
        lambda ids: {
            company_id: CompanyDocumentInventory(company_id=company_id + 1),
        },
    )
    with pytest.raises(ValueError, match="another company"):
        analyze_opportunity_readiness(opportunity_id)
    assess.assert_not_called()


def test_persistence_failure_rolls_back_checklist_and_summary_together(
    setup_readiness, isolated_session_factory
):
    opportunity_id, _, _, assess = setup_readiness
    analyze_opportunity_readiness(opportunity_id)
    with isolated_session_factory() as db:
        original = db.scalar(select(CompanyOpportunityReadiness)).readiness_fingerprint
        original_notes = list(db.scalars(select(OpportunityRequirement.notes)))
        db.scalar(select(OpportunityPreference)).intelligence = {
            "track_record": ["Updated evidence"]
        }
        db.commit()
    assess.side_effect = lambda **kw: assessment(
        kw["requirements"], experience="KNOWN_MISSING"
    )
    engine = isolated_session_factory.kw["bind"]

    def fail_on_item_update(_connection, _cursor, statement, *_args):
        if statement.startswith("UPDATE opportunity_requirements"):
            raise RuntimeError("Simulated checklist write failure")

    event.listen(engine, "before_cursor_execute", fail_on_item_update)
    try:
        assert analyze_opportunity_readiness(opportunity_id)[0].status == "ERROR"
    finally:
        event.remove(engine, "before_cursor_execute", fail_on_item_update)
    with isolated_session_factory() as db:
        assert (
            db.scalar(select(CompanyOpportunityReadiness)).readiness_fingerprint
            == original
        )
        assert list(db.scalars(select(OpportunityRequirement.notes))) == original_notes


def test_company_selection_never_mixes_checklists(
    monkeypatch, isolated_session_factory, client
):
    _configure(monkeypatch, isolated_session_factory)
    opportunity_id, companies = _seed(
        isolated_session_factory,
        analysis=analysis(),
        companies=[
            {"name": "First Company"},
            {"name": "Second Company"},
        ],
    )
    analyze_opportunity_readiness(
        opportunity_id,
        extractor=Mock(return_value=extraction(analysis())),
        analyzer=Mock(
            side_effect=[
                assessment(extraction(analysis())),
                assessment(extraction(analysis()), experience="KNOWN_MISSING"),
            ]
        ),
    )
    first = client.get(f"/opportunities/{opportunity_id}?company_id={companies[0]}")
    second = client.get(f"/opportunities/{opportunity_id}?company_id={companies[1]}")
    assert "Approximately 50%" in first.text
    assert "Approximately 0%" in second.text
    assert "Only two qualifying assignments" not in first.text
    assert "Only two qualifying assignments" in second.text


def test_optional_inapplicable_requirement_is_excluded_from_summary():
    from app.intelligence.readiness_engine import (
        summarize_readiness,
        validate_readiness,
    )

    definition = extraction(
        analysis(
            required_documents=[],
            experience_requirements=["If bidding jointly, name a consortium lead"],
        )
    )
    definition.requirements[0].required = None
    assessed = assessment(definition, experience="UNKNOWN")
    assessed.items[0].status = RequirementStatus.NOT_APPLICABLE
    assessed = ReadinessAnalysis.model_validate(assessed.model_dump())
    validate_readiness(assessed, definition, CompanyDocumentInventory(company_id=1))
    summary = summarize_readiness(assessed, definition)
    assert (
        summary.ready_count == summary.missing_count == summary.confirmation_count == 0
    )
    assert summary.overall_status.value == "UNKNOWN"
    assert summary.readiness_score is None
