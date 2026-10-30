"""Explicit analysis/reanalysis operations. Never called by GET/rendering paths."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from sqlalchemy import select

from app import database
from app.intelligence import company_documents
from app.intelligence.company_documents import (
    CompanyDocumentInventory,
    document_snapshot,
)
from app.intelligence.gemini_client import load_gemini_config
from app.intelligence.downstream_diagnostics import DownstreamFailure, record_failure
from app.intelligence.matching_engine import (
    _load_verified_analysis,
    load_active_company_profiles,
)
from app.intelligence.readiness_analyzer import (
    EXTRACTION_VERSION,
    READINESS_VERSION,
    analyze_readiness,
    extract_requirements,
)
from app.intelligence.readiness_schemas import (
    Availability,
    ReadinessAnalysis,
    ReadinessStatus,
    ReadinessSummary,
    RequirementExtraction,
    RequirementStatus,
    analysis_fingerprint,
    fingerprint,
    requirement_key,
    validate_extraction,
)
from app.intelligence.schemas import OpportunityAnalysis
from app.models import (
    CompanyOpportunityReadiness,
    OpportunityRequirement,
    OpportunityRequirementExtraction,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadinessOutcome:
    company_id: int
    status: str
    summary: ReadinessSummary | None
    error_category: str | None = None


def validate_readiness(
    result: ReadinessAnalysis,
    requirements: RequirementExtraction,
    documents: CompanyDocumentInventory,
) -> ReadinessAnalysis:
    if not isinstance(result, ReadinessAnalysis):
        raise ValueError("Invalid readiness response")
    result = ReadinessAnalysis.model_validate(result.model_dump())
    definitions = {requirement_key(item): item for item in requirements.requirements}
    ids = [item.requirement_id for item in result.items]
    if len(ids) != len(set(ids)) or set(ids) != set(definitions):
        raise ValueError(
            "Readiness must assess each supported requirement exactly once"
        )
    document_records = {
        document.record_id: document for document in documents.documents
    }
    if len(document_records) != len(documents.documents):
        raise ValueError("Duplicate document metadata identifiers")
    for item in result.items:
        definition = definitions[item.requirement_id]
        if not set(item.document_ids).issubset(document_records):
            raise ValueError("Unsupported document reference")
        if (
            item.status
            in {RequirementStatus.MISSING, RequirementStatus.NEEDS_CONFIRMATION}
            and not item.next_action
        ):
            raise ValueError("Outstanding items need a practical next action")
        if item.status is RequirementStatus.NOT_APPLICABLE:
            if definition.required is True:
                raise ValueError(
                    "Mandatory requirements cannot be marked not applicable"
                )
            continue
        if (
            item.status is RequirementStatus.READY
            and item.availability is not Availability.KNOWN_AVAILABLE
        ):
            raise ValueError("Readiness needs positive evidence")
        if (
            item.status is RequirementStatus.MISSING
            and item.availability is not Availability.KNOWN_MISSING
        ):
            raise ValueError("Missing is not the same as unknown")
        if definition.requires_document:
            referenced = [
                document_records[record_id].availability
                for record_id in item.document_ids
            ]
            if Availability.KNOWN_AVAILABLE in referenced:
                available = Availability.KNOWN_AVAILABLE
            elif referenced and all(
                state is Availability.KNOWN_MISSING for state in referenced
            ):
                available = Availability.KNOWN_MISSING
            elif not referenced and documents.complete:
                available = Availability.KNOWN_MISSING
            else:
                available = Availability.UNKNOWN
            if item.availability is not available:
                raise ValueError("Document availability contradicts company records")
            if (
                available is Availability.UNKNOWN
                and item.status is not RequirementStatus.NEEDS_CONFIRMATION
            ):
                raise ValueError("Unknown documents need confirmation")
            if (
                available is Availability.KNOWN_MISSING
                and item.status is not RequirementStatus.MISSING
            ):
                raise ValueError("Known missing documents must be shown as missing")
    return result


def summarize_readiness(
    result: ReadinessAnalysis, extraction: RequirementExtraction
) -> ReadinessSummary:
    """Rounded operational coverage, not a probability of bid success.

    Score = ready / applicable checklist items, rounded to five points. Unknown
    and not-applicable-only checklists have no score. Critical known gaps override
    aggregate coverage; mandatory readiness cannot be hidden by optional items.
    """
    definitions = {requirement_key(item): item for item in extraction.requirements}
    ready = sum(item.status is RequirementStatus.READY for item in result.items)
    missing = sum(item.status is RequirementStatus.MISSING for item in result.items)
    confirmation = sum(
        item.status is RequirementStatus.NEEDS_CONFIRMATION for item in result.items
    )
    critical = sum(
        item.status is RequirementStatus.MISSING
        and definitions[item.requirement_id].critical
        for item in result.items
    )
    total = ready + missing + confirmation
    score = round(ready / total * 20) * 5 if total and (ready or missing) else None
    if not total or (not ready and not missing):
        status = ReadinessStatus.UNKNOWN
    elif ready == total:
        status = ReadinessStatus.READY
    elif critical or missing >= total / 2:
        status = ReadinessStatus.SIGNIFICANT_GAPS
    elif not missing and ready / total >= 0.8:
        status = ReadinessStatus.MOSTLY_READY
    else:
        status = ReadinessStatus.NEEDS_WORK
    actions = list(
        dict.fromkeys(
            item.next_action
            for item in result.items
            if item.status
            in {RequirementStatus.MISSING, RequirementStatus.NEEDS_CONFIRMATION}
            and item.next_action
        )
    )
    return ReadinessSummary(
        readiness_score=score,
        ready_count=ready,
        missing_count=missing,
        confirmation_count=confirmation,
        critical_gap_count=critical,
        overall_status=status,
        next_actions=actions,
    )


def _extraction(
    opportunity_id: int,
    analysis: OpportunityAnalysis,
    extractor: Callable[..., RequirementExtraction],
) -> tuple[RequirementExtraction | None, str, DownstreamFailure | None]:
    source_hash = analysis_fingerprint(analysis)
    extraction_hash = fingerprint(
        [source_hash, EXTRACTION_VERSION, load_gemini_config().model]
    )
    with database.SessionLocal() as db:
        record = db.get(OpportunityRequirementExtraction, opportunity_id)
        if (
            record
            and record.extraction_fingerprint == extraction_hash
            and not record.error_reason
        ):
            try:
                return (
                    validate_extraction(
                        RequirementExtraction.model_validate(record.requirements_data),
                        analysis,
                    ),
                    extraction_hash,
                    None,
                )
            except ValueError:
                pass
    error = None
    failure = None
    extracted = None
    try:
        extracted = validate_extraction(extractor(opportunity=analysis), analysis)
    except Exception as exc:
        extracted = None
        failure = record_failure(
            exc, stage="REQUIREMENTS", schema=RequirementExtraction
        )
        error = failure.serialize()
    try:
        with database.SessionLocal() as db:
            record = db.get(OpportunityRequirementExtraction, opportunity_id)
            if record is None:
                record = OpportunityRequirementExtraction(opportunity_id=opportunity_id)
                db.add(record)
            record.analysis_fingerprint = source_hash
            record.extraction_fingerprint = extraction_hash
            record.requirements_data = (
                extracted.model_dump(mode="json") if extracted else None
            )
            record.error_reason = error
            record.extracted_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        extracted = None
        failure = record_failure(
            exc,
            stage="REQUIREMENTS",
            schema=RequirementExtraction,
            persistence=True,
        )
    return extracted, extraction_hash, failure


def _persist(
    *,
    opportunity_id: int,
    company_id: int,
    analysis: OpportunityAnalysis,
    extraction: RequirementExtraction | None,
    extraction_hash: str,
    profile_hash: str,
    documents_hash: str,
    readiness_hash: str,
    result: ReadinessAnalysis | None,
    summary: ReadinessSummary | None,
    error: str | None,
) -> None:
    """Atomically update one company's checklist and summary; keep stable item IDs."""
    with database.SessionLocal() as db:
        record = db.scalar(
            select(CompanyOpportunityReadiness).where(
                CompanyOpportunityReadiness.opportunity_id == opportunity_id,
                CompanyOpportunityReadiness.company_id == company_id,
            )
        )
        if record is None:
            record = CompanyOpportunityReadiness(
                opportunity_id=opportunity_id, company_id=company_id
            )
            db.add(record)
        record.analysis_fingerprint = analysis_fingerprint(analysis)
        record.extraction_fingerprint = extraction_hash
        record.profile_fingerprint = profile_hash
        record.documents_fingerprint = documents_hash
        record.readiness_fingerprint = readiness_hash
        record.result_data = summary.model_dump(mode="json") if summary else None
        record.error_reason = error
        record.assessed_at = datetime.now(timezone.utc)
        if result is not None and extraction is not None:
            previous = {
                row.requirement_key: row
                for row in db.scalars(
                    select(OpportunityRequirement).where(
                        OpportunityRequirement.company_id == company_id,
                        OpportunityRequirement.opportunity_id == opportunity_id,
                    )
                )
            }
            assessments = {item.requirement_id: item for item in result.items}
            for definition in extraction.requirements:
                key = requirement_key(definition)
                item = assessments[key]
                row = previous.pop(key, None)
                if row is None:
                    row = OpportunityRequirement(
                        company_id=company_id,
                        opportunity_id=opportunity_id,
                        requirement_key=key,
                    )
                    db.add(row)
                row.category = definition.category.value
                row.requirement = definition.requirement
                row.source_field = definition.source_field
                row.source_url = analysis.original_source_url
                row.evidence = definition.evidence
                row.required = definition.required
                row.critical = definition.critical
                row.requires_document = definition.requires_document
                row.status = item.status.value
                row.availability = item.availability.value
                row.confidence = min(definition.confidence, item.confidence)
                row.notes = item.notes
                row.next_action = item.next_action
                row.document_ids = list(item.document_ids)
            for obsolete in previous.values():
                db.delete(obsolete)
        db.commit()


def analyze_opportunity_readiness(
    opportunity_id: int,
    *,
    company_ids: list[int] | None = None,
    extractor: Callable[..., RequirementExtraction] | None = None,
    analyzer: Callable[..., ReadinessAnalysis] | None = None,
) -> list[ReadinessOutcome]:
    """Explicit, cache-aware operation; no source discovery, rematching or crawling."""
    profiles = load_active_company_profiles(company_ids)
    if not profiles:
        raise ValueError("An active company profile is required")
    analysis = _load_verified_analysis(opportunity_id)
    extraction, extraction_hash, extraction_failure = _extraction(
        opportunity_id, analysis, extractor or extract_requirements
    )
    inventories = company_documents.load_company_documents(
        [profile.company_id for profile in profiles]
    )
    with database.SessionLocal() as db:
        cached_records = {
            row.company_id: row
            for row in db.scalars(
                select(CompanyOpportunityReadiness).where(
                    CompanyOpportunityReadiness.opportunity_id == opportunity_id,
                )
            )
        }
    outcomes: list[ReadinessOutcome] = []
    for profile in profiles:
        documents = document_snapshot(
            inventories[profile.company_id], datetime.now(timezone.utc).date()
        )
        if documents.company_id != profile.company_id:
            raise ValueError("Document inventory belongs to another company")
        profile_hash = fingerprint(profile.profile)
        documents_hash = fingerprint(documents.model_dump(mode="json"))
        readiness_hash = fingerprint(
            [
                extraction_hash,
                extraction.model_dump(mode="json") if extraction else None,
                profile_hash,
                documents_hash,
                READINESS_VERSION,
                load_gemini_config().model,
            ]
        )
        cached = cached_records.get(profile.company_id)
        if (
            extraction is not None
            and cached
            and not cached.error_reason
            and cached.readiness_fingerprint == readiness_hash
        ):
            try:
                cached_summary = ReadinessSummary.model_validate(cached.result_data)
            except ValueError:
                pass
            else:
                outcomes.append(
                    ReadinessOutcome(profile.company_id, "CACHED", cached_summary)
                )
                continue
        result = None
        summary = None
        error = None
        failure = extraction_failure
        try:
            if extraction is None:
                raise ValueError("Requirement extraction unavailable")
            result = validate_readiness(
                (analyzer or analyze_readiness)(
                    opportunity=analysis,
                    requirements=extraction,
                    company_profile=profile.profile,
                    documents=documents,
                )
                if extraction.requirements
                else ReadinessAnalysis(items=[]),
                extraction,
                documents,
            )
            summary = summarize_readiness(result, extraction)
        except Exception as exc:
            result = None
            summary = None
            failure = failure or record_failure(
                exc, stage="READINESS", schema=ReadinessAnalysis
            )
            error = failure.serialize()
        try:
            _persist(
                opportunity_id=opportunity_id,
                company_id=profile.company_id,
                analysis=analysis,
                extraction=extraction,
                extraction_hash=extraction_hash,
                profile_hash=profile_hash,
                documents_hash=documents_hash,
                readiness_hash=readiness_hash,
                result=result,
                summary=summary,
                error=error,
            )
        except Exception as exc:
            failure = record_failure(
                exc,
                stage="READINESS",
                schema=ReadinessAnalysis,
                persistence=True,
            )
            error = failure.serialize()
            summary = None
        outcomes.append(
            ReadinessOutcome(
                profile.company_id,
                "ERROR" if error else "ANALYZED",
                summary,
                failure.category if failure else None,
            )
        )
    return outcomes
