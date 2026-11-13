"""Restore two previously tested companies in the sole beta-admin workspace.

This is an explicit, idempotent operator task. It never replaces an existing
profile and does not assign any company to a different workspace.
Business service statements are reconstructed from prior user specifications;
historic private contracts are not embedded in public source code.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.beta_models import BetaUser
from app.company_profiles import CompanyIntelligence
from app.database import SessionLocal
from app.models import OpportunityPreference, Organization

PROFILES = (
    {
        "name": "UT CPA LTD",
        "country": "Rwanda",
        "city": "Kigali",
        "industry": "Audit, accounting and financial advisory",
        "description": (
            "Rwanda-based professional services firm providing external and "
            "donor-funded project audits, accounting, tax advisory and "
            "compliance, internal audit, financial governance, financial "
            "management and institutional or economic advisory. Also undertakes "
            "feasibility studies, business planning, surveys and capacity building."
        ),
        "services": [
            "External financial audit and assurance",
            "Donor-funded project and grant audit",
            "Internal audit and risk management",
            "Accounting and financial reporting",
            "Tax compliance and advisory",
            "Financial management and internal controls",
            "Financial feasibility studies and business planning",
            "Financial and institutional advisory",
            "Survey research, governance assessments and capacity building",
        ],
        "industries": [
            "Professional services",
            "Public sector",
            "Non-governmental and development organizations",
            "Financial services",
            "Agriculture and agribusiness",
        ],
        "keywords": [
            "external audit", "donor audit", "financial audit",
            "financial management", "tax", "accounting",
            "internal control", "feasibility", "financial advisory",
            "business plan", "institutional assessment",
        ],
        "intelligence": {
            "capabilities": [
                "External audit, financial assurance and accounting",
                "Tax advisory and compliance reviews",
                "Financial and institutional assessments",
                "Financial feasibility, business plans and advisory",
                "Survey research, governance assessment and capacity development",
            ],
            "areas_of_expertise": [
                "Audit and assurance", "Financial controls", "Financial analysis",
                "Taxation", "Donor reporting and compliance",
                "Financial governance and institutional assessment",
            ],
            "assignment_types": [
                "External audit", "Financial review", "Tax health check",
                "Financial feasibility assessment",
                "Institutional performance assessment",
                "Research survey", "Business and financial strategy",
            ],
            "client_types": [
                "Public institutions", "Non-governmental organizations",
                "Donor-financed projects", "Financial institutions",
                "Private sector clients",
            ],
            "sector_experience": [
                "Public administration", "Development programmes",
                "Financial services", "Agriculture",
            ],
            "relevant_experience": [
                "Audit and financial advisory mandates, feasibility assessments, "
                "survey-based institutional reviews and strategic financial advice."
            ],
            "preferred_sectors": [
                "Finance and governance", "Public sector", "Development programmes",
                "Agriculture and agribusiness", "Financial institutions",
            ],
            "excluded_services": [
                "Goods supply without professional advisory scope",
                "Construction works without a financial or advisory component",
                "Individual employment vacancies",
            ],
            "excluded_opportunity_types": ["Individual employment vacancy"],
        },
    },
    {
        "name": "UT CONSULTING LTD",
        "country": "Rwanda",
        "city": "Kigali",
        "industry": "Research, development and management consulting",
        "description": (
            "Rwanda-based multidisciplinary consulting company focused on "
            "research and surveys, strategy, institutional development, "
            "monitoring and evaluation, social inclusion, capacity building, "
            "stakeholder engagement and environmental/social safeguards advisory. "
            "Work also encompasses feasibility and planning studies for "
            "development programmes and public institutions."
        ),
        "services": [
            "Research, surveys and data analysis",
            "Monitoring, evaluation and learning",
            "Project baseline, endline and impact assessments",
            "Institutional development and strategy",
            "Capacity needs assessment and capacity building",
            "Social inclusion and stakeholder consultation",
            "Environmental and social safeguards advisory",
            "ESIA and RAP advisory and related studies",
            "Development programme and infrastructure feasibility studies",
            "Policy research and management consulting",
        ],
        "industries": [
            "Management consulting", "Development cooperation",
            "Environment and infrastructure", "Public sector",
            "Agriculture", "Health and education",
        ],
        "keywords": [
            "consultancy", "evaluation", "baseline survey", "impact assessment",
            "research", "monitoring and evaluation", "M&E",
            "strategy", "capacity development", "institutional development",
            "environmental and social impact", "ESIA", "RAP",
            "stakeholder engagement", "feasibility",
        ],
        "intelligence": {
            "capabilities": [
                "Development and institutional consulting",
                "Primary research, stakeholder consultations and data analysis",
                "Monitoring, evaluation, baseline and impact assessments",
                "Strategy, HR development and institutional capacity planning",
                "Environmental and social safeguards and feasibility advisory",
            ],
            "areas_of_expertise": [
                "Social and institutional development", "M&E",
                "Research and evaluation", "Environmental and social safeguards",
                "Strategy and capacity development",
                "Stakeholder engagement and policy studies",
            ],
            "assignment_types": [
                "Consultancy", "Feasibility study", "Baseline and endline study",
                "Programme evaluation", "Institutional strategy",
                "Environmental or social assessment",
                "Policy study", "Capacity needs assessment",
            ],
            "client_types": [
                "Public institutions", "Development programmes",
                "Non-governmental organizations", "Development partners",
            ],
            "sector_experience": [
                "Public sector", "Environment and social development",
                "Agriculture", "Infrastructure", "Education", "Health",
            ],
            "relevant_experience": [
                "Studies and advisory assignments concerning social inclusion "
                "and institutional development, including work carried out "
                "through project partnerships or consortia."
            ],
            "preferred_sectors": [
                "Environment", "Development cooperation", "Public sector",
                "Agriculture", "Water and infrastructure", "Education",
                "Health", "Institutional development",
            ],
            "excluded_services": [
                "Statutory financial audit", "Tax and bookkeeping services",
                "Pure goods supply", "Individual employment vacancies",
            ],
            "excluded_opportunity_types": ["Individual employment vacancy"],
        },
    },
)


def main() -> None:
    with SessionLocal() as db:
        administrators = list(db.scalars(
            select(BetaUser).where(
                BetaUser.is_active.is_(True), BetaUser.is_admin.is_(True)
            )
        ))
        if len(administrators) != 1 or administrators[0].workspace_id is None:
            raise RuntimeError(
                "Cannot safely infer the destination workspace: "
                "expected exactly one active administrator with a workspace."
            )
        workspace_id = administrators[0].workspace_id
        added = 0
        for item in PROFILES:
            existing = db.scalar(
                select(Organization).where(
                    Organization.workspace_id == workspace_id,
                    func.lower(Organization.name) == item["name"].lower(),
                )
            )
            if existing is not None:
                print(f"PRESERVED {item['name']}: existing profile left unchanged")
                continue
            other = db.scalar(
                select(Organization).where(
                    func.lower(Organization.name) == item["name"].lower()
                )
            )
            if other is not None:
                raise RuntimeError(
                    "A profile with this name exists in another workspace; "
                    "refusing to reassign or duplicate it."
                )
            org = Organization(
                workspace_id=workspace_id,
                name=item["name"],
                country=item["country"],
                city=item["city"],
                industry=item["industry"],
                description=item["description"],
                default_language="en",
                timezone="Africa/Kigali",
                is_active=True,
            )
            db.add(org)
            db.flush()
            intelligence = CompanyIntelligence.model_validate(
                item["intelligence"]
            ).model_dump()
            db.add(OpportunityPreference(
                organization_id=org.id,
                countries=["Rwanda"],
                regions=[],
                industries=list(item["industries"]),
                services=list(item["services"]),
                opportunity_types=["TENDER", "RFP", "RFQ", "EOI", "CONSULTANCY"],
                keywords=list(item["keywords"]),
                languages=[],
                minimum_match_score=70,
                include_no_deadline=True,
                include_jobs=False,
                include_tenders=True,
                include_grants=False,
                include_partnerships=True,
                intelligence=intelligence,
            ))
            added += 1
            print(f"CREATED {item['name']} in administrator workspace")
        db.commit()
        count = db.scalar(
            select(func.count(Organization.id)).where(
                Organization.workspace_id == workspace_id,
                Organization.is_active.is_(True),
            )
        )
        print(f"RESTORE COMPLETE created={added} active_owned_companies={count}")


if __name__ == "__main__":
    main()
