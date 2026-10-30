from sqlalchemy import select
from bs4 import BeautifulSoup
import pytest

from app.models import OpportunityPreference, Organization


def test_rich_profile_round_trip_and_explicit_clear(client, isolated_session_factory):
    response = client.post("/business-profile", data={
        "name": "Evaluation Partners", "description": "Programme reviews",
        "countries": "Kenya, Uganda", "services": "Evaluation, Research",
        "opportunity_types": "Consultancy, RFP", "minimum_match_score": "82",
        "rich_profile": "true", "capabilities": " Field research \nfield research\n",
        "areas_of_expertise": "Agricultural economics",
        "assignment_types": "Impact evaluations", "client_types": "NGOs",
        "sector_experience": "Agriculture", "relevant_experience": "Five reviews",
        "track_record": "Programme A, delivered in 2025",
        "preferred_sectors": "Rural development", "excluded_services": "Building works",
        "excluded_sectors": "Mining", "excluded_opportunity_types": "Goods supply",
        "excluded_countries": "Unsupported market",
    })
    assert response.status_code == 200
    assert "Business profile saved successfully" in response.text
    assert "Programme A, delivered in 2025" in response.text
    assert "<summary>Exclusions</summary>" in response.text
    with isolated_session_factory() as db:
        preference = db.scalar(select(OpportunityPreference))
        assert preference.countries == ["Kenya", "Uganda"]
        assert preference.minimum_match_score == 82
        assert preference.intelligence["capabilities"] == ["Field research"]
        assert preference.intelligence["excluded_sectors"] == ["Mining"]
        original = dict(preference.intelligence)

    # A legacy client does not know about the new fields: preserve them.
    client.post("/business-profile", data={"name": "Evaluation Partners"})
    with isolated_session_factory() as db:
        assert db.scalar(select(OpportunityPreference)).intelligence == original

    client.post("/business-profile", data={
        "name": "Evaluation Partners", "rich_profile": "true",
        "capabilities": "", "excluded_sectors": "",
    })
    with isolated_session_factory() as db:
        intelligence = db.scalar(select(OpportunityPreference)).intelligence
        assert all(value == [] for value in intelligence.values())


def test_editing_selected_company_does_not_change_another(
    client, isolated_session_factory,
):
    with isolated_session_factory() as db:
        first = Organization(name="First Company", is_active=True)
        second = Organization(name="Second Company", is_active=True)
        db.add_all([first, second])
        db.flush()
        first_id, second_id = first.id, second.id
        db.add_all([
            OpportunityPreference(organization_id=first_id,
                                  intelligence={"capabilities": ["Original"]}),
            OpportunityPreference(organization_id=second_id),
        ])
        db.commit()

    page = client.get(f"/business-profile?company_id={second_id}")
    assert page.status_code == 200
    assert page.context["organization"].id == second_id
    assert len(page.context["companies"]) == 2
    response = client.post("/business-profile", data={
        "organization_id": str(second_id), "name": "Second Company updated",
        "rich_profile": "true", "capabilities": "Programme design",
    })
    assert response.status_code == 200
    with isolated_session_factory() as db:
        assert db.get(Organization, first_id).name == "First Company"
        prefs = {p.organization_id: p for p in db.scalars(select(OpportunityPreference))}
        assert prefs[first_id].intelligence == {"capabilities": ["Original"]}
        assert prefs[second_id].intelligence["capabilities"] == ["Programme design"]
    assert client.get("/business-profile?company_id=99999").status_code == 404
    assert client.post("/business-profile", data={
        "organization_id": "99999", "name": "Must not create",
    }).status_code == 404


def _existing_company(factory):
    with factory() as db:
        company = Organization(name="Existing Advisory", country="Kenya", is_active=True)
        db.add(company)
        db.flush()
        identifier = company.id
        db.add(OpportunityPreference(
            organization_id=identifier, services=["Audit"], countries=["Kenya"],
            minimum_match_score=84, intelligence={"capabilities": ["Original capability"]},
        ))
        db.commit()
    return identifier


def test_add_company_form_is_blank_and_does_not_modify_existing_profile(
    client, isolated_session_factory,
):
    identifier = _existing_company(isolated_session_factory)
    existing = client.get("/business-profile")
    assert 'href="/business-profile?new=true"' in existing.text
    page = client.get("/business-profile?new=true")
    assert page.status_code == 200
    assert page.context["organization"] is None
    assert page.context["preference"] is None
    assert page.context["create_new"] is True
    form = BeautifulSoup(page.text, "html.parser").select_one("form.business-profile-form")
    assert form.select_one('[name="organization_id"]') is None
    assert form.select_one('[name="create_new"]')["value"] == "true"
    for field in ("name", "country", "website", "services", "countries"):
        assert form.select_one(f'[name="{field}"]')["value"] == ""
    assert form.select_one('[name="capabilities"]').get_text() == ""
    assert "Create company" in form.get_text()
    with isolated_session_factory() as db:
        assert [c.id for c in db.scalars(select(Organization))] == [identifier]


def test_create_company_then_edit_keeps_existing_company_and_preferences(
    client, isolated_session_factory,
):
    identifier = _existing_company(isolated_session_factory)
    response = client.post("/business-profile", data={
        "create_new": "true", "rich_profile": "true", "name": "  Evaluation Partners  ",
        "country": "Uganda", "countries": "Uganda, Kenya", "services": "Evaluation, Research",
        "capabilities": "Impact evaluation\nField research", "sector_experience": "Agriculture",
        "relevant_experience": "Programme evaluation", "excluded_services": "Construction",
        "opportunity_types": "Consultancy", "minimum_match_score": "0",
    }, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    created_id = page.context["organization"].id
    assert created_id != identifier
    assert page.context["organization"].is_active is True
    assert len(page.context["companies"]) == 2
    form = BeautifulSoup(page.text, "html.parser").select_one("form.business-profile-form")
    assert form.select_one('[name="create_new"]') is None
    assert form.select_one('[name="organization_id"]')["value"] == str(created_id)
    with isolated_session_factory() as db:
        preferences = {p.organization_id: p for p in db.scalars(select(OpportunityPreference))}
        assert preferences[created_id].countries == ["Uganda", "Kenya"]
        assert preferences[created_id].services == ["Evaluation", "Research"]
        assert preferences[created_id].intelligence["capabilities"] == ["Impact evaluation", "Field research"]
        assert preferences[created_id].minimum_match_score == 0
    edited = client.post("/business-profile", data={
        "organization_id": str(created_id), "name": "Evaluation Partners Updated",
    })
    assert edited.status_code == 200
    with isolated_session_factory() as db:
        assert len(list(db.scalars(select(Organization)))) == 2
        assert db.get(Organization, identifier).name == "Existing Advisory"
        assert db.get(Organization, identifier).country == "Kenya"
        original = db.scalar(select(OpportunityPreference).where(
            OpportunityPreference.organization_id == identifier
        ))
        assert original.services == ["Audit"]
        assert original.countries == ["Kenya"]
        assert original.minimum_match_score == 84
        assert original.intelligence == {"capabilities": ["Original capability"]}
        assert db.get(Organization, created_id).name == "Evaluation Partners Updated"


@pytest.mark.parametrize("case", ["conflicting-get", "conflicting-post", "blank-name"])
def test_invalid_creation_requests_leave_existing_company_untouched(
    client, isolated_session_factory, case,
):
    identifier = _existing_company(isolated_session_factory)
    if case == "conflicting-get":
        response = client.get(f"/business-profile?new=true&company_id={identifier}")
    else:
        data = {"create_new": "true", "name": "Must not replace"}
        if case == "conflicting-post":
            data["organization_id"] = str(identifier)
        else:
            data["name"] = "   "
        response = client.post("/business-profile", data=data)
    assert response.status_code == 400
    with isolated_session_factory() as db:
        assert [c.name for c in db.scalars(select(Organization))] == ["Existing Advisory"]


def test_first_profile_form_uses_explicit_creation(client, isolated_session_factory):
    page = client.get("/business-profile")
    assert page.status_code == 200
    assert page.context["create_new"] is True
    response = client.post("/business-profile", data={
        "create_new": "true", "name": "First Company", "rich_profile": "true",
    })
    assert response.status_code == 200
    with isolated_session_factory() as db:
        company = db.scalar(select(Organization))
        assert company.name == "First Company"
        assert company.is_active is True
