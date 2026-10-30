"""Real middleware/session journeys; external intelligence is mocked at its boundary."""

from datetime import timedelta
import json
import re
import sys
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.beta_models import BetaSession, BetaUser, Workspace, WorkspaceOpportunity
from app.beta_scope import Principal, principal
from app.beta_security import CSRF_COOKIE, SESSION_COOKIE, digest, hash_password, utcnow
from app.models import Opportunity, OpportunityPreference, Organization


@pytest.fixture
def beta(isolated_session_factory, monkeypatch):
    from app import main, scanner
    from app.intelligence import (
        matching_engine,
        opportunity_pipeline,
        readiness_engine,
        readiness_store,
        triage_engine,
    )
    from app.intelligence.gemini_client import GeminiClient
    from app.intelligence.schemas import CompanyMatch

    for module in (
        scanner,
        matching_engine,
        opportunity_pipeline,
        readiness_engine,
        readiness_store,
        triage_engine,
    ):
        if hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", isolated_session_factory)
    # Also cover store modules imported by those engines.
    for name, module in list(sys.modules.items()):
        if name.startswith("app.intelligence.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", isolated_session_factory)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
    calls = []

    def generate(self, prompt, response_schema, **kwargs):
        name = response_schema.__name__
        calls.append(name)
        if name == "OpportunityAnalysis":
            values = dict(
                decision="ACCEPT",
                confidence=95,
                reason="Specific active notice",
                title="Independent controls review",
                buyer_name="Public Buyer",
                opportunity_type="CONSULTANCY",
                status="ACTIVE",
                deadline="2099-12-31",
                summary="Review financial controls",
                required_documents=["Tax clearance"],
            )
        elif response_schema is CompanyMatch:
            company_id = int(re.search(r"Company ID: (\d+)", prompt).group(1))
            values = dict(
                company_id=company_id,
                score=91,
                match_strength="STRONG",
                explanation="Relevant assurance services",
                recommendation="Review the requirements",
            )
        elif name == "TriageResult":
            values = dict(
                priority="HIGH",
                priority_score=85,
                bid_readiness="UNKNOWN",
                urgency="LOW",
                estimated_effort="UNKNOWN",
                key_reasons=["Relevant work"],
                critical_gaps=[],
                eligibility_risks=[],
                recommended_next_action="Review requirements",
                budget_evidence="UNKNOWN",
                deadline_evidence="CONFIRMED",
                eligibility_evidence="UNKNOWN",
                experience_evidence="UNKNOWN",
                exclusion_applies=False,
                required_documents=[],
            )
        elif name == "RequirementExtraction":
            values = {
                "requirements": [
                    dict(
                        category="TAX",
                        requirement="Tax clearance",
                        source_field="required_documents",
                        evidence="Tax clearance",
                        required=True,
                        critical=False,
                        requires_document=True,
                        confidence=95,
                    )
                ]
            }
        elif name == "ReadinessAnalysis":
            values = {
                "items": [
                    dict(
                        requirement_id=item["id"],
                        status="NEEDS_CONFIRMATION",
                        availability="UNKNOWN",
                        confidence=90,
                        notes="No document evidence supplied",
                        next_action="Confirm certificate availability",
                        document_ids=[],
                    )
                    for item in json.loads(prompt)["requirements"]
                ]
            }
        else:
            raise AssertionError(name)
        return response_schema.model_validate(values)

    monkeypatch.setattr(GeminiClient, "generate_structured", generate)
    monkeypatch.setattr(
        GeminiClient,
        "generate_text",
        Mock(side_effect=AssertionError("Unexpected AI operation")),
    )
    with isolated_session_factory() as db:
        for index in (1, 2):
            db.add(Workspace(id=index, name=f"Workspace {index}"))
            db.flush()
            db.add(
                BetaUser(
                    id=index,
                    workspace_id=index,
                    email=f"owner{index}@example.test",
                    password_hash=hash_password("a long beta password"),
                    is_active=True,
                    is_admin=False,
                    invite_hash=digest(f"invite-{index}"),
                    invite_expires_at=utcnow() + timedelta(hours=1),
                )
            )
        db.commit()
    main.app.middleware_stack = None
    with (
        TestClient(main.app, follow_redirects=False) as first,
        TestClient(main.app, follow_redirects=False) as second,
    ):
        yield first, second, calls, isolated_session_factory


def post(client, path, **data):
    client.get("/settings" if client.cookies.get(SESSION_COOKIE) else "/login")
    return client.post(
        path, data={"csrf_token": client.cookies.get(CSRF_COOKIE), **data}
    )


def sign_in(client, index=1):
    response = post(
        client,
        "/login",
        email=f"owner{index}@example.test",
        password="a long beta password",
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/"


def create_company(client, name):
    response = post(
        client,
        "/business-profile",
        name=name,
        description="Financial controls and assurance",
        countries="Kenya, Uganda",
        services="Assurance",
        capabilities="Financial review",
        create_new="true",
        rich_profile="true",
    )
    assert response.status_code == 303, response.text
    return int(response.headers["location"].split("company_id=")[1])


def test_private_beta_complete_journey_and_two_workspace_isolation(beta):
    first, second, calls, factory = beta
    sign_in(first)
    company = create_company(first, "First private company")
    profile = first.get(f"/business-profile?company_id={company}")
    assert profile.status_code == 200
    assert "Kenya" in profile.text and "Uganda" in profile.text
    notice = "Independent controls review\nQualified firms invited. Tax clearance required. Deadline 31 December 2099."
    submitted = post(first, "/analyze", company_id=company, notice_text=notice)
    assert submitted.status_code == 303, submitted.text
    assert "/opportunities/" in submitted.headers["location"]
    detail_url = submitted.headers["location"]
    oid = int(detail_url.split("/")[2].split("?")[0])
    assert calls[:3] == ["OpportunityAnalysis", "CompanyMatch", "TriageResult"]
    before = list(calls)
    feed = first.get("/")
    assert feed.status_code == 200, feed.text
    assert "Independent controls review" in feed.text
    detail = first.get(detail_url)
    assert detail.status_code == 200
    assert "Relevant assurance services" in detail.text
    assert calls == before
    assert post(first, f"/opportunities/{oid}/pipeline/add").status_code == 303
    assert (
        post(
            first,
            f"/opportunities/{oid}/pipeline/update",
            lead_status="Pursuing",
            next_action="PRIVATE next action",
            lead_notes="PRIVATE pursuit notes",
        ).status_code
        == 303
    )
    assert "PRIVATE next action" in first.get("/pipeline").text
    ready = post(first, f"/opportunities/{oid}/readiness/analyze", company_id=company)
    assert ready.status_code == 303
    detail = first.get(detail_url)
    assert "Tax clearance" in detail.text
    assert "Confirm certificate availability" in detail.text
    assert "Unknown" in detail.text or "UNKNOWN" in detail.text
    assert "RequirementExtraction" in calls and "ReadinessAnalysis" in calls
    before = list(calls)
    for route in (
        "/",
        "/pipeline",
        "/business-profile",
        "/sources",
        detail_url,
        "/assessments",
        "/settings",
    ):
        assert first.get(route).status_code == 200
    assert calls == before
    # Repeat submission reuses the existing analysis and match fingerprints.
    assert (
        post(first, "/analyze", company_id=company, notice_text=notice).status_code
        == 303
    )
    assert calls.count("OpportunityAnalysis") == before.count("OpportunityAnalysis")
    assert calls.count("CompanyMatch") == before.count("CompanyMatch")
    sign_in(second, 2)
    other_company = create_company(second, "Second private company")
    assert "First private company" not in second.get("/").text
    assert "PRIVATE pursuit notes" not in second.get("/pipeline").text
    for path in (
        f"/business-profile?company_id={company}",
        f"/?company_id={company}",
        detail_url,
        f"/opportunities/{oid}",
    ):
        assert second.get(path).status_code == 404, path
    for path, data in (
        ("/business-profile", dict(name="Attack", organization_id=company)),
        (f"/opportunities/{oid}/pipeline/add", {}),
        (f"/opportunities/{oid}/update", dict(status="Seen")),
        (f"/opportunities/{oid}/readiness/analyze", dict(company_id=company)),
        ("/analyze", dict(company_id=company, notice_text=notice)),
    ):
        assert post(second, path, **data).status_code == 404, path
    assert "Independent controls review" not in second.get("/assessments").text
    other = post(second, "/analyze", company_id=other_company, notice_text=notice)
    assert other.status_code == 303
    assert other.headers["location"] != detail_url
    assert second.get(other.headers["location"]).status_code == 200
    # Ownership is persisted, not inferred from a browser-selected company.
    with factory() as db:
        companies = list(db.scalars(select(Organization).order_by(Organization.id)))
        assert [item.workspace_id for item in companies] == [1, 2]
        assert len(list(db.scalars(select(WorkspaceOpportunity)))) == 1


def test_authentication_csrf_invites_and_admin_boundaries(beta):
    first, second, calls, factory = beta
    for path in (
        "/",
        "/pipeline",
        "/business-profile",
        "/sources",
        "/opportunities/1",
        "/assessments",
        "/settings",
        "/docs",
        "/openapi.json",
    ):
        assert first.get(path).headers["location"] == "/login"
    assert first.get("/healthz").json() == {"status": "ok"}
    assert (
        first.post(
            "/login",
            data={"email": "owner1@example.test", "password": "a long beta password"},
        ).status_code
        == 403
    )
    first.get("/accept-invite")
    accepted = post(
        first, "/accept-invite", token="invite-1", password="an activated password"
    )
    assert accepted.status_code == 303
    assert "HttpOnly" in accepted.headers["set-cookie"]
    assert (
        post(
            second,
            "/accept-invite",
            token="invite-1",
            password="a replacement password",
        ).status_code
        == 200
    )
    assert (
        "invalid or expired"
        in post(
            second,
            "/accept-invite",
            token="invite-1",
            password="a replacement password",
        ).text
    )
    assert first.post("/scan", data={"csrf_token": "forged"}).status_code == 403
    for path in ("/scan", "/ai-review", "/sources/1/enable", "/sources/1/disable"):
        assert post(first, path).status_code == 403
    assert first.get("/docs").status_code == 403
    first.get("/settings")
    assert (
        first.post(
            "/logout",
            data={"csrf_token": first.cookies.get(CSRF_COOKIE)},
            headers={"origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert post(first, "/logout").status_code == 303
    assert first.get("/pipeline").headers["location"] == "/login"
    assert not calls
    with factory() as db:
        assert not list(db.scalars(select(BetaSession)))


def test_shared_notice_has_independent_private_pipeline_and_legacy_notes_hidden(beta):
    first, second, calls, factory = beta
    with factory() as db:
        item = Opportunity(
            title="Public shared notice",
            category="RFP",
            source_name="Portal",
            source_url="https://public.example.test/notice",
            user_notes="OLD PRIVATE NOTES",
            lead_notes="OLD LEAD NOTES",
            is_lead=True,
        )
        db.add(item)
        db.commit()
        oid = item.id
    sign_in(first)
    sign_in(second, 2)
    assert "OLD PRIVATE NOTES" not in first.get(f"/opportunities/{oid}").text
    assert "OLD LEAD NOTES" not in second.get("/pipeline").text
    assert post(first, f"/opportunities/{oid}/pipeline/add").status_code == 303
    assert (
        post(
            first,
            f"/opportunities/{oid}/pipeline/update",
            lead_status="Pursuing",
            next_action="WORKSPACE ONE ONLY",
        ).status_code
        == 303
    )
    assert "WORKSPACE ONE ONLY" in first.get("/pipeline").text
    assert "WORKSPACE ONE ONLY" not in second.get("/pipeline").text
    assert "WORKSPACE ONE ONLY" not in second.get(f"/opportunities/{oid}").text
    assert post(second, f"/opportunities/{oid}/pipeline/add").status_code == 303
    assert (
        post(
            second,
            f"/opportunities/{oid}/pipeline/update",
            lead_status="On Hold",
            next_action="WORKSPACE TWO ONLY",
        ).status_code
        == 303
    )
    assert "WORKSPACE TWO ONLY" not in first.get("/pipeline").text
    with factory() as db:
        assert db.get(Opportunity, oid).user_notes == "OLD PRIVATE NOTES"
        assert len(list(db.scalars(select(WorkspaceOpportunity)))) == 2
    assert calls == []


def test_fetch_and_provider_failures_are_safe_and_operation_limit_is_durable(
    beta, monkeypatch
):
    first, second, calls, factory = beta
    from app import beta_submission

    sign_in(first)
    company = create_company(first, "Private company")
    monkeypatch.setattr(
        beta_submission,
        "fetch_notice",
        Mock(side_effect=ValueError("SECRET_CONNECTION_DETAIL")),
    )
    response = post(
        first,
        "/analyze",
        company_id=company,
        url="https://unavailable.example.test/notice",
    )
    assert response.status_code == 502
    assert "Paste the notice text" in response.text
    assert "SECRET_CONNECTION_DETAIL" not in response.text
    for _ in range(9):
        assert (
            post(
                first,
                "/analyze",
                company_id=company,
                url="https://unavailable.example.test/notice",
            ).status_code
            == 502
        )
    assert (
        post(first, "/analyze", company_id=company, notice_text="notice").status_code
        == 429
    )
    assert not calls


def test_scoping_direct_queries_and_foreign_workspace_mutations(beta):
    first, second, calls, factory = beta
    with factory() as db:
        db.add_all(
            [
                Organization(id=1, name="one", workspace_id=1),
                Organization(id=2, name="two", workspace_id=2),
            ]
        )
        db.flush()
        db.add_all(
            [
                OpportunityPreference(organization_id=1, countries=[]),
                OpportunityPreference(organization_id=2, countries=[]),
            ]
        )
        db.commit()
    token = principal.set(Principal(1, 1, "owner1@example.test", False))
    try:
        with factory() as db:
            assert db.get(Organization, 2) is None
            assert len(list(db.scalars(select(OpportunityPreference)))) == 1
            assert list(db.scalars(select(Organization.id))) == [1]
            db.add(Organization(name="forged", workspace_id=2))
            with pytest.raises(PermissionError):
                db.commit()
    finally:
        principal.reset(token)


def test_needs_review_and_ai_failure_have_private_retryable_receipts(beta, monkeypatch):
    first, second, calls, factory = beta
    from app.intelligence import opportunity_pipeline
    from app.intelligence.gemini_client import GeminiAPIError
    from app.intelligence.schemas import OpportunityAnalysis

    sign_in(first)
    company = create_company(first, "Review workspace company")
    review = OpportunityAnalysis(
        decision="REVIEW",
        confidence=40,
        reason="Deadline and scope need confirmation",
        opportunity_type="UNKNOWN",
        status="UNKNOWN",
    )
    monkeypatch.setattr(
        opportunity_pipeline, "analyze_opportunity", Mock(return_value=review)
    )
    response = post(
        first,
        "/analyze",
        company_id=company,
        notice_text="Unclear notice requiring review",
    )
    assert response.headers["location"] == "/assessments?result=REVIEW"
    assert "Deadline and scope need confirmation" in first.get("/assessments").text
    assert "Unclear notice requiring review" not in first.get("/").text
    monkeypatch.setattr(
        opportunity_pipeline,
        "analyze_opportunity",
        Mock(side_effect=GeminiAPIError("SECRET_PROVIDER_ERROR")),
    )
    response = post(
        first,
        "/analyze",
        company_id=company,
        notice_text="A separate notice with a provider failure",
    )
    assert response.headers["location"] == "/assessments?result=ERROR"
    receipt = first.get("/assessments")
    assert "Submit again to retry" in receipt.text
    assert "SECRET_PROVIDER_ERROR" not in receipt.text
    sign_in(second, 2)
    assert "Unclear notice requiring review" not in second.get("/assessments").text


def test_selected_source_scan_is_bounded_and_workspace_scoped(beta, monkeypatch):
    first, second, calls, factory = beta
    from app import beta_routes
    from app.models import Source
    from app.schemas import RawOpportunity

    sign_in(first)
    company = create_company(first, "Source scan company")
    with factory() as db:
        source = Source(
            base_url="https://source.example.test/",
            monitor_url="https://source.example.test/notices",
            domain="source.example.test",
            is_active=True,
            is_approved=True,
        )
        db.add(source)
        db.commit()
        sid = source.id
    candidates = [
        RawOpportunity(
            None,
            f"Notice {n}",
            "Shared source",
            f"https://source.example.test/notice-{n}",
            description=f"Controls review tender {n}",
        )
        for n in range(5)
    ]
    fetch = Mock(return_value=candidates)
    monkeypatch.setattr(beta_routes, "fetch_source_candidates", fetch)
    process = Mock(return_value=(None, "REVIEW"))
    monkeypatch.setattr(beta_routes, "process_raw_notice", process)
    assert "Scan selected source" in first.get("/sources").text
    response = post(first, "/scan-source", company_id=company, source_id=sid)
    assert response.status_code == 303
    assert process.call_count == 3
    fetch.assert_called_once()
    fetch.reset_mock()
    sign_in(second, 2)
    assert (
        post(second, "/scan-source", company_id=company, source_id=sid).status_code
        == 404
    )
    fetch.assert_not_called()
    monkeypatch.setattr(beta_routes, "fetch_source_candidates", Mock(return_value=[]))
    assert (
        post(first, "/scan-source", company_id=company, source_id=sid).status_code
        == 502
    )
    assert calls == []


def test_only_admin_can_add_shared_sources_without_network_or_ai(beta):
    first, second, calls, factory = beta
    sign_in(first)
    assert (
        post(
            first,
            "/sources/add",
            name="Public source",
            url="https://agency.example.test/notices",
        ).status_code
        == 403
    )
    with factory() as db:
        db.get(BetaUser, 1).is_admin = True
        db.commit()
    assert (
        post(
            first, "/sources/add", name="Unsafe", url="http://127.0.0.1/private"
        ).status_code
        == 400
    )
    for _ in range(2):
        assert (
            post(
                first,
                "/sources/add",
                name="Public source",
                url="https://agency.example.test/notices",
            ).status_code
            == 303
        )
    assert "Public source" in first.get("/sources").text
    with factory() as db:
        from app.models import Source

        sources = list(db.scalars(select(Source)))
        assert len(sources) == 1
        assert sources[0].source_role == "UNKNOWN"
    assert not calls
