"""Read-only, batched readiness rendering with stale-input detection."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intelligence import company_documents
from app.intelligence.company_documents import document_snapshot
from app.intelligence.gemini_client import load_gemini_config
from app.intelligence.matching_engine import build_company_profile
from app.intelligence.readiness_analyzer import READINESS_VERSION
from app.intelligence.readiness_schemas import (
    ReadinessSummary,
    analysis_fingerprint,
    fingerprint,
)
from app.intelligence.schemas import OpportunityAnalysis
from app.models import (
    AIOpportunityAnalysis,
    CompanyOpportunityReadiness,
    OpportunityPreference,
    OpportunityRequirement,
    OpportunityRequirementExtraction,
    Organization,
)


@dataclass
class StoredReadiness:
    company_id: int
    company_name: str
    summary: ReadinessSummary | None
    requirements: list[OpportunityRequirement] = field(default_factory=list)


def load_stored_readiness(
    db: Session,
    opportunity_ids: set[int],
    *,
    company_id: int | None = None,
    include_items: bool = False,
) -> dict[tuple[int, int], StoredReadiness]:
    if not opportunity_ids:
        return {}
    statement = (
        select(
            CompanyOpportunityReadiness,
            Organization,
            OpportunityPreference,
            OpportunityRequirementExtraction,
        )
        .join(Organization, Organization.id == CompanyOpportunityReadiness.company_id)
        .outerjoin(
            OpportunityPreference,
            OpportunityPreference.organization_id == Organization.id,
        )
        .outerjoin(
            OpportunityRequirementExtraction,
            OpportunityRequirementExtraction.opportunity_id
            == CompanyOpportunityReadiness.opportunity_id,
        )
        .where(
            CompanyOpportunityReadiness.opportunity_id.in_(opportunity_ids),
            Organization.is_active.is_(True),
        )
        .order_by(Organization.name, Organization.id)
    )
    if company_id is not None:
        statement = statement.where(Organization.id == company_id)
    rows = db.execute(statement).all()
    if not rows:
        return {}
    analyses: dict[int, OpportunityAnalysis] = {}
    for record in db.scalars(
        select(AIOpportunityAnalysis)
        .where(
            AIOpportunityAnalysis.opportunity_id.in_(opportunity_ids),
            AIOpportunityAnalysis.analysis_data.is_not(None),
        )
        .order_by(AIOpportunityAnalysis.id)
    ):
        if record.opportunity_id is not None and record.opportunity_id not in analyses:
            try:
                analyses[record.opportunity_id] = OpportunityAnalysis.model_validate(
                    record.analysis_data
                )
            except ValueError:
                continue
    inventories = company_documents.load_company_documents(
        sorted({company.id for _, company, _, _ in rows})
    )
    results: dict[tuple[int, int], StoredReadiness] = {}
    for stored, company, preference, extraction in rows:
        analysis = analyses.get(stored.opportunity_id)
        inventory = document_snapshot(
            inventories[company.id], datetime.now(timezone.utc).date()
        )
        current = (
            not stored.error_reason
            and extraction is not None
            and not extraction.error_reason
            and analysis is not None
            and stored.analysis_fingerprint == analysis_fingerprint(analysis)
            and stored.extraction_fingerprint == extraction.extraction_fingerprint
            and stored.profile_fingerprint
            == fingerprint(build_company_profile(company, preference).profile)
            and inventory.company_id == company.id
            and stored.documents_fingerprint
            == fingerprint(inventory.model_dump(mode="json"))
            and stored.readiness_fingerprint
            == fingerprint(
                [
                    extraction.extraction_fingerprint,
                    extraction.requirements_data,
                    stored.profile_fingerprint,
                    stored.documents_fingerprint,
                    READINESS_VERSION,
                    load_gemini_config().model,
                ]
            )
        )
        summary = None
        if current:
            try:
                summary = ReadinessSummary.model_validate(stored.result_data)
            except ValueError:
                pass
        results[(stored.opportunity_id, company.id)] = StoredReadiness(
            company.id, company.name, summary
        )
    if include_items:
        item_query = select(OpportunityRequirement).where(
            OpportunityRequirement.opportunity_id.in_(opportunity_ids)
        )
        if company_id is not None:
            item_query = item_query.where(
                OpportunityRequirement.company_id == company_id
            )
        for item in db.scalars(item_query.order_by(OpportunityRequirement.id)):
            view = results.get((item.opportunity_id, item.company_id))
            if view is not None and view.summary is not None:
                view.requirements.append(item)
    return results
