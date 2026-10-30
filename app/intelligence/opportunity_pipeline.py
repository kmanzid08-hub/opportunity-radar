from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.intelligence.gemini_client import GeminiError
from app.intelligence.opportunity_analyzer import analyze_opportunity
from app.intelligence.schemas import (
    AnalysisDecision,
    OpportunityAnalysis,
    OpportunityStatus,
    OpportunityType,
    SourceRole,
)
from app.intelligence.provenance import (
    public_url,
    resolve_original,
    same_domain,
    same_issuer,
)
from app.models import AIOpportunityAnalysis, Opportunity, Source
from app.schemas import FilteredOpportunity, RawOpportunity


MINIMUM_VERIFIED_CONFIDENCE = 70

# Bump for material analyzer, prompt, or response-schema changes. This version
# requires the Phase 9D geography/provenance analysis without expiring other stages.
OPPORTUNITY_ANALYSIS_VERSION = "5-clause-level-applicant-evidence"

BUSINESS_OPPORTUNITY_TYPES = frozenset(
    {
        OpportunityType.PROCUREMENT,
        OpportunityType.TENDER,
        OpportunityType.RFP,
        OpportunityType.RFQ,
        OpportunityType.EOI,
        OpportunityType.CONSULTANCY,
        OpportunityType.GRANT,
        OpportunityType.CALL_FOR_PROPOSALS,
        OpportunityType.PROFESSIONAL_SERVICE,
        OpportunityType.FRAMEWORK,
        OpportunityType.OTHER_OPPORTUNITY,
    }
)

EXPLICIT_NON_OPPORTUNITY_TYPES = frozenset(
    {
        OpportunityType.JOB_VACANCY,
        OpportunityType.ARTICLE,
        OpportunityType.NEWS,
        OpportunityType.TRAINING,
        OpportunityType.EVENT,
        OpportunityType.DIRECTORY,
        OpportunityType.MARKETING,
        OpportunityType.AWARD_NOTICE,
        OpportunityType.CLOSED_OPPORTUNITY,
        OpportunityType.INFORMATION_PAGE,
    }
)

OpportunityAnalyzer = Callable[..., OpportunityAnalysis]


class OpportunityProcessingStatus(str, Enum):
    VERIFIED = "VERIFIED"
    REVIEW = "REVIEW"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class OpportunityProcessingOutcome:
    status: OpportunityProcessingStatus
    analysis: OpportunityAnalysis | None
    analyzed: bool
    filtered_opportunity: FilteredOpportunity | None = None
    duplicate_opportunity_id: int | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def build_opportunity_content_fingerprint(
    opportunity: RawOpportunity,
    *,
    country_context: str | None = None,
) -> str:
    """Version analyzer input; keep URL-independent cross-page cache reuse."""
    meaningful_content = "\n".join(
        (
            _normalize_text(opportunity.title),
            _normalize_text(opportunity.organisation_name),
            _normalize_text(opportunity.source_name),
            _normalize_text(opportunity.description),
            opportunity.deadline.isoformat() if opportunity.deadline else "",
            _normalize_text(country_context or opportunity.country_context),
        )
    ).casefold()
    versioned_content = f"{OPPORTUNITY_ANALYSIS_VERSION}\n{meaningful_content}"
    return sha256(versioned_content.encode("utf-8")).hexdigest()


def build_semantic_opportunity_fingerprint(
    analysis: OpportunityAnalysis,
) -> str:
    """Build a practical cross-page duplicate key from extracted meaning."""
    identity = {
        "buyer": analysis.buyer_name or analysis.issuing_organisation,
        "country": analysis.country,
        "deadline": analysis.deadline.isoformat() if analysis.deadline else None,
        "deliverables": analysis.deliverables,
        "scope": analysis.scope,
        "services": analysis.services_required,
        "title": analysis.title,
        "type": analysis.opportunity_type.value,
    }
    normalized = _normalize_text(
        json.dumps(identity, ensure_ascii=False, sort_keys=True)
    ).casefold()
    return sha256(normalized.encode("utf-8")).hexdigest()


def opportunity_quality_status(
    analysis: OpportunityAnalysis,
) -> OpportunityProcessingStatus:
    """Apply the deterministic gate after semantic classification."""
    if (
        analysis.decision is AnalysisDecision.REJECT
        or analysis.opportunity_type in EXPLICIT_NON_OPPORTUNITY_TYPES
        or analysis.status in {OpportunityStatus.CLOSED, OpportunityStatus.EXPIRED}
    ):
        return OpportunityProcessingStatus.REJECTED

    if (
        analysis.decision is AnalysisDecision.REVIEW
        or analysis.opportunity_type not in BUSINESS_OPPORTUNITY_TYPES
        or analysis.confidence < MINIMUM_VERIFIED_CONFIDENCE
    ):
        return OpportunityProcessingStatus.REVIEW

    sufficiently_specific = bool(
        analysis.title
        and (
            analysis.summary
            or analysis.scope
            or analysis.services_required
            or analysis.deliverables
        )
    )
    if not sufficiently_specific:
        return OpportunityProcessingStatus.REVIEW

    return OpportunityProcessingStatus.VERIFIED


def _to_filtered_opportunity(
    raw: RawOpportunity,
    analysis: OpportunityAnalysis,
) -> FilteredOpportunity:
    evidence = "; ".join(analysis.evidence)
    match_reason = analysis.reason
    if evidence:
        match_reason = f"{match_reason} Evidence: {evidence}"

    return FilteredOpportunity(
        organisation_name=(
            analysis.issuing_organisation
            or analysis.buyer_name
            or raw.organisation_name
        ),
        title=analysis.title or raw.title,
        category=analysis.opportunity_type.value,
        source_name=raw.source_name,
        source_url=raw.source_url,
        match_score=analysis.confidence,
        match_reason=match_reason,
        description=analysis.summary or analysis.scope or raw.description,
        deadline=analysis.deadline,
    )


def _analysis_from_record(
    record: AIOpportunityAnalysis,
) -> OpportunityAnalysis | None:
    if record.analysis_data is None:
        return None
    try:
        return OpportunityAnalysis.model_validate(record.analysis_data)
    except (TypeError, ValueError):
        return None


def load_canonical_analyses(
    db: Session, opportunity_ids: set[int]
) -> dict[int, OpportunityAnalysis]:
    """One batched read: prefer issuer content, then the most recent analysis."""
    if not opportunity_ids:
        return {}
    records = db.scalars(
        select(AIOpportunityAnalysis)
        .where(
            AIOpportunityAnalysis.opportunity_id.in_(opportunity_ids),
            AIOpportunityAnalysis.analysis_data.is_not(None),
        )
        .order_by(
            AIOpportunityAnalysis.analyzed_at.desc(), AIOpportunityAnalysis.id.desc()
        )
    )
    result: dict[int, OpportunityAnalysis] = {}
    for record in records:
        analysis = _analysis_from_record(record)
        if analysis is None or record.opportunity_id is None:
            continue
        previous = result.get(record.opportunity_id)
        if previous is None or (
            previous.source_role is not SourceRole.AUTHORITATIVE
            and analysis.source_role is SourceRole.AUTHORITATIVE
        ):
            result[record.opportunity_id] = analysis
    return result


def _resolve_provenance(
    raw: RawOpportunity, analysis: OpportunityAnalysis
) -> OpportunityAnalysis:
    with SessionLocal() as db:
        sources = list(db.scalars(select(Source).where(Source.is_approved.is_(True))))
    current = next(
        (source for source in sources if same_domain(raw.source_url, source.base_url)),
        None,
    )
    role = (
        SourceRole(current.source_role)
        if current and current.source_role in SourceRole._value2member_map_
        else SourceRole.UNKNOWN
    )
    if role is SourceRole.UNKNOWN:
        # Page role is semantic evidence; authority still requires an observed
        # issuer-domain relationship, not merely the model's label.
        role = (
            analysis.source_role
            if analysis.source_role is not SourceRole.AUTHORITATIVE
            else SourceRole.UNKNOWN
        )
    domains = [
        source.base_url
        for source in sources
        if source.source_role == SourceRole.AUTHORITATIVE.value
        and (
            same_issuer(source.organisation_name, analysis.buyer_name)
            or same_issuer(source.organisation_name, analysis.issuing_organisation)
        )
    ]
    original = resolve_original(
        analysis=analysis,
        discovered_url=raw.source_url,
        links=raw.outgoing_links,
        source_role=role,
        authoritative_domains=domains,
    )
    if original == public_url(raw.source_url) and original is not None:
        role = SourceRole.AUTHORITATIVE
    elif original and role is SourceRole.UNKNOWN:
        role = SourceRole.REPOST
    return analysis.model_copy(
        update={
            "original_source_url": original,
            "discovered_from_url": raw.source_url,
            "source_role": role,
        }
    )


def _cached_analysis(
    *,
    source_url: str,
    content_fingerprint: str,
) -> tuple[OpportunityAnalysis, int | None, str] | None:
    with SessionLocal() as db:
        same_url = db.scalar(
            select(AIOpportunityAnalysis).where(
                AIOpportunityAnalysis.source_url == source_url,
                AIOpportunityAnalysis.content_fingerprint == content_fingerprint,
            )
        )
        if same_url is not None:
            analysis = _analysis_from_record(same_url)
            if analysis is not None:
                return analysis, same_url.opportunity_id, same_url.source_url

        identical_content = db.scalar(
            select(AIOpportunityAnalysis)
            .where(
                AIOpportunityAnalysis.content_fingerprint == content_fingerprint,
                AIOpportunityAnalysis.analysis_data.is_not(None),
            )
            .order_by(AIOpportunityAnalysis.id.asc())
        )
        if identical_content is None:
            return None
        analysis = _analysis_from_record(identical_content)
        if analysis is None:
            return None
        return (
            analysis,
            identical_content.opportunity_id,
            identical_content.source_url,
        )


def _persist_analysis(
    *,
    raw: RawOpportunity,
    content_fingerprint: str,
    analysis: OpportunityAnalysis,
    duplicate_hint: int | None = None,
) -> int | None:
    opportunity_fingerprint = build_semantic_opportunity_fingerprint(analysis)
    with SessionLocal() as db:
        duplicate_id = duplicate_hint
        if duplicate_id is None and analysis.original_source_url:
            duplicate_id = db.scalar(
                select(Opportunity.id)
                .where(
                    or_(
                        Opportunity.original_source_url == analysis.original_source_url,
                        Opportunity.source_url == analysis.original_source_url,
                    )
                )
                .limit(1)
            )
        if duplicate_id is None:
            duplicate_id = db.scalar(
                select(Opportunity.id)
                .where(
                    or_(
                        Opportunity.source_url == raw.source_url,
                        Opportunity.original_source_url
                        == (public_url(raw.source_url) or raw.source_url),
                    )
                )
                .limit(1)
            )
        if duplicate_id is None:
            duplicate_id = db.scalar(
                select(AIOpportunityAnalysis.opportunity_id)
                .where(
                    AIOpportunityAnalysis.opportunity_fingerprint
                    == opportunity_fingerprint,
                    AIOpportunityAnalysis.opportunity_id.is_not(None),
                    AIOpportunityAnalysis.source_url != raw.source_url,
                )
                .limit(1)
            )

        record = db.scalar(
            select(AIOpportunityAnalysis).where(
                AIOpportunityAnalysis.source_url == raw.source_url
            )
        )
        if record is None:
            record = AIOpportunityAnalysis(
                source_url=raw.source_url,
                raw_title=raw.title,
                source_name=raw.source_name,
                content_fingerprint=content_fingerprint,
                analyzed_at=_utc_now(),
            )
            db.add(record)

        record.raw_title = raw.title
        record.source_name = raw.source_name
        record.content_fingerprint = content_fingerprint
        record.opportunity_fingerprint = opportunity_fingerprint
        record.decision = analysis.decision.value
        record.confidence = analysis.confidence
        record.opportunity_type = analysis.opportunity_type.value
        record.opportunity_status = analysis.status.value
        record.analysis_data = analysis.model_dump(mode="json")
        record.error_reason = None
        record.analyzed_at = _utc_now()
        if duplicate_id is not None:
            record.opportunity_id = duplicate_id
            if (
                analysis.original_source_url
                and opportunity_quality_status(analysis)
                is OpportunityProcessingStatus.VERIFIED
            ):
                opportunity = db.get(Opportunity, duplicate_id)
                if opportunity is not None:
                    opportunity.original_source_url = analysis.original_source_url
        db.commit()
        return duplicate_id


def _persist_error(
    *,
    raw: RawOpportunity,
    content_fingerprint: str,
    reason: str,
) -> None:
    with SessionLocal() as db:
        record = db.scalar(
            select(AIOpportunityAnalysis).where(
                AIOpportunityAnalysis.source_url == raw.source_url
            )
        )
        if record is None:
            record = AIOpportunityAnalysis(
                source_url=raw.source_url,
                raw_title=raw.title,
                source_name=raw.source_name,
                content_fingerprint=content_fingerprint,
                analyzed_at=_utc_now(),
            )
            db.add(record)

        record.raw_title = raw.title
        record.source_name = raw.source_name
        record.content_fingerprint = content_fingerprint
        record.opportunity_fingerprint = None
        record.decision = None
        record.confidence = None
        record.opportunity_type = None
        record.opportunity_status = None
        record.analysis_data = None
        record.error_reason = reason
        record.analyzed_at = _utc_now()
        db.commit()


def _outcome(
    *,
    raw: RawOpportunity,
    analysis: OpportunityAnalysis,
    analyzed: bool,
    duplicate_opportunity_id: int | None,
) -> OpportunityProcessingOutcome:
    status = opportunity_quality_status(analysis)
    filtered = None
    if (
        status is OpportunityProcessingStatus.VERIFIED
        and duplicate_opportunity_id is None
    ):
        filtered = _to_filtered_opportunity(raw, analysis)

    return OpportunityProcessingOutcome(
        status=status,
        analysis=analysis,
        analyzed=analyzed,
        filtered_opportunity=filtered,
        duplicate_opportunity_id=duplicate_opportunity_id,
    )


def process_opportunity_candidate(
    raw: RawOpportunity,
    *,
    country_context: str | None = None,
    analyzer: OpportunityAnalyzer | None = None,
) -> OpportunityProcessingOutcome:
    """Analyze, cache, quality-gate, and prepare one crawler candidate."""
    effective_country = country_context or raw.country_context
    content_fingerprint = build_opportunity_content_fingerprint(
        raw,
        country_context=effective_country,
    )
    cached = _cached_analysis(
        source_url=raw.source_url,
        content_fingerprint=content_fingerprint,
    )
    if cached is not None:
        analysis, cached_opportunity_id, cached_source_url = cached
        resolved = _resolve_provenance(raw, analysis)
        if cached_source_url != raw.source_url or resolved != analysis:
            cached_opportunity_id = _persist_analysis(
                raw=raw,
                content_fingerprint=content_fingerprint,
                analysis=resolved,
                duplicate_hint=cached_opportunity_id,
            )
        analysis = resolved
        return _outcome(
            raw=raw,
            analysis=analysis,
            analyzed=False,
            duplicate_opportunity_id=cached_opportunity_id,
        )

    analyze: OpportunityAnalyzer = analyzer or analyze_opportunity
    try:
        arguments: dict[str, object] = dict(
            source_url=raw.source_url,
            page_title=raw.title,
            page_content=raw.description or raw.title,
            source_name=raw.source_name,
            source_organisation=raw.organisation_name,
            country_hint=effective_country,
        )
        if raw.outgoing_links:
            arguments["outgoing_links"] = raw.outgoing_links
        analysis = analyze(**arguments)
    except GeminiError:
        _persist_error(
            raw=raw,
            content_fingerprint=content_fingerprint,
            reason="Gemini opportunity analysis failed.",
        )
        return OpportunityProcessingOutcome(
            status=OpportunityProcessingStatus.ERROR,
            analysis=None,
            analyzed=True,
        )

    if not isinstance(analysis, OpportunityAnalysis):
        _persist_error(
            raw=raw,
            content_fingerprint=content_fingerprint,
            reason="Gemini returned an invalid opportunity analysis.",
        )
        return OpportunityProcessingOutcome(
            status=OpportunityProcessingStatus.ERROR,
            analysis=None,
            analyzed=True,
        )

    analysis = _resolve_provenance(raw, analysis)

    duplicate_id = _persist_analysis(
        raw=raw,
        content_fingerprint=content_fingerprint,
        analysis=analysis,
    )
    return _outcome(
        raw=raw,
        analysis=analysis,
        analyzed=True,
        duplicate_opportunity_id=duplicate_id,
    )


def link_analysis_to_saved_opportunity(source_url: str) -> int | None:
    """Link the durable AI record after scanner persistence succeeds."""
    with SessionLocal() as db:
        record = db.scalar(
            select(AIOpportunityAnalysis).where(
                AIOpportunityAnalysis.source_url == source_url
            )
        )
        opportunity_id = db.scalar(
            select(Opportunity.id).where(Opportunity.source_url == source_url)
        )
        if record is not None and opportunity_id is not None:
            record.opportunity_id = opportunity_id
            analysis = _analysis_from_record(record)
            opportunity = db.get(Opportunity, opportunity_id)
            if opportunity is not None and analysis and analysis.original_source_url:
                opportunity.original_source_url = analysis.original_source_url
            db.commit()
            return opportunity_id
    return None
