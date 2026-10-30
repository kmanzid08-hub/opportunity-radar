from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, cast

from fastapi import (
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, case, func, or_, select, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings
from app.company_profiles import CompanyIntelligence, INTELLIGENCE_SECTIONS
from app.database import SessionLocal
from app.beta_scope import principal, private_state, pursuit_column, pursuit_expired
from app.beta_security import BetaSecurityMiddleware, install_error_handlers, validate_production
from app.ai_review import run_ai_review
from app.business_matching import (
    BusinessMatchPreferences,
    score_for_business,
)
from app.models import (
    AIOpportunityAnalysis,
    CompanyOpportunityMatch,
    LEAD_PRIORITIES,
    LEAD_STATUSES,
    OPPORTUNITY_STATUSES,
    Opportunity,
    OpportunityPreference,
    Organization,
    Source,
)
from app.intelligence.schemas import OpportunityAnalysis
from app.intelligence.match_store import load_current_profiles, match_is_current
from app.intelligence.readiness_engine import analyze_opportunity_readiness
from app.intelligence.readiness_store import load_stored_readiness
from app.intelligence.triage_store import (
    PRIORITY_ORDER,
    load_stored_triage,
    stored_triage_result,
)
from app.schemas import FilteredOpportunity
from app.internal_scheduler import scheduler
from app.scanner import run_scanner


BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_PAGE_SIZE = 50


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_production()
    scheduler_enabled = (
        get_settings().enable_internal_scheduler
    )

    if scheduler_enabled:
        scheduler.start()

    try:
        yield

    finally:
        if scheduler_enabled:
            scheduler.stop()


app = FastAPI(
    title="Opportunity Radar",
    description=(
        "Business opportunity discovery "
        "and pursuit management platform"
    ),
    lifespan=lifespan,
)


app.add_middleware(BetaSecurityMiddleware)
install_error_handlers(app)

app.mount(
    "/static",
    StaticFiles(
        directory=BASE_DIR / "static"
    ),
    name="static",
)


templates = Jinja2Templates(
    directory=BASE_DIR / "templates"
)


def current_utc_time() -> datetime:
    return datetime.now(
        timezone.utc
    )


def parse_list_field(
    value: str,
) -> list[str]:
    """
    Convert comma-separated form input into
    a clean de-duplicated list.
    """

    items: list[str] = []
    seen: set[str] = set()

    for raw_item in value.split(","):
        item = raw_item.strip()

        if not item:
            continue

        key = item.casefold()

        if key in seen:
            continue

        seen.add(key)
        items.append(item)

    return items


def opportunity_archive_condition(
    now: datetime,
) -> ColumnElement[bool]:
    """
    Return the rule for records hidden
    from the opportunity inbox.
    """

    settings = get_settings()

    today = now.date()

    stale_before = now - timedelta(
        days=(
            settings
            .inbox_no_deadline_max_age_days
        )
    )

    last_activity = func.coalesce(
        Opportunity.last_seen_at,
        Opportunity.first_discovered_at,
        Opportunity.created_at,
    )

    return and_(
        pursuit_column("is_lead").is_(False),
        or_(
            pursuit_expired().is_(True),

            and_(
                Opportunity.deadline.is_not(
                    None
                ),
                Opportunity.deadline < today,
            ),

            and_(
                Opportunity.deadline.is_(None),
                last_activity.is_not(None),
                last_activity < stale_before,
            ),
        ),
    )


def opportunity_inbox_condition(
    now: datetime,
) -> ColumnElement[bool]:
    """
    Return records that remain actionable
    in the opportunity inbox.
    """

    settings = get_settings()

    today = now.date()

    stale_before = now - timedelta(
        days=(
            settings
            .inbox_no_deadline_max_age_days
        )
    )

    last_activity = func.coalesce(
        Opportunity.last_seen_at,
        Opportunity.first_discovered_at,
        Opportunity.created_at,
    )

    return or_(
        pursuit_column("is_lead").is_(True),

        and_(
            pursuit_column("is_lead").is_(False),
            pursuit_expired().is_(False),

            or_(
                Opportunity.deadline >= today,

                and_(
                    Opportunity.deadline.is_(None),

                    or_(
                        last_activity.is_(None),
                        last_activity >= stale_before,
                    ),
                ),
            ),
        ),
    )


def get_dashboard_metrics(
    now: datetime,
) -> dict[str, int | float]:
    """
    Return operational metrics shown
    on the dashboard.
    """

    today = now.date()

    tomorrow = now + timedelta(
        days=1
    )

    start_of_today = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    start_of_tomorrow = tomorrow.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    start_of_week = (
        start_of_today
        - timedelta(
            days=today.weekday()
        )
    )

    expiring_before = (
        today
        + timedelta(days=7)
    )

    expired_condition = or_(
        pursuit_expired().is_(True),

        and_(
            Opportunity.deadline.is_not(
                None
            ),
            Opportunity.deadline < today,
        ),
    )

    active_condition = and_(
        opportunity_inbox_condition(now),
        pursuit_expired().is_(False),

        or_(
            Opportunity.deadline.is_(None),
            Opportunity.deadline >= today,
        ),
    )

    expiring_soon_condition = and_(
        pursuit_expired().is_(False),
        Opportunity.deadline.is_not(None),
        Opportunity.deadline >= today,
        Opportunity.deadline
        <= expiring_before,
    )

    def count_when(
        condition: ColumnElement[bool],
    ):
        return func.sum(
            case(
                (
                    condition,
                    1,
                ),
                else_=0,
            )
        )

    with SessionLocal() as db:
        opportunity_counts = db.execute(
            select(
                count_when(
                    and_(
                        Opportunity
                        .first_discovered_at
                        >= start_of_today,

                        Opportunity
                        .first_discovered_at
                        < start_of_tomorrow,
                    )
                ).label(
                    "new_today"
                ),

                count_when(
                    and_(
                        Opportunity
                        .first_discovered_at
                        >= start_of_week,

                        Opportunity
                        .first_discovered_at
                        < start_of_tomorrow,
                    )
                ).label(
                    "new_this_week"
                ),

                count_when(
                    active_condition
                ).label(
                    "active"
                ),

                count_when(
                    expiring_soon_condition
                ).label(
                    "expiring_soon"
                ),

                count_when(
                    expired_condition
                ).label(
                    "expired"
                ),

                count_when(
                    opportunity_archive_condition(
                        now
                    )
                ).label(
                    "archived"
                ),

                count_when(
                    pursuit_column("is_lead").is_(
                        True
                    )
                ).label(
                    "pipeline"
                ),
            )
        ).one()

        scanned_today_condition = and_(
            Source.last_scanned_at.is_not(
                None
            ),
            Source.last_scanned_at
            >= start_of_today,
            Source.last_scanned_at
            < start_of_tomorrow,
        )

        latest_scan_succeeded = and_(
            scanned_today_condition,
            Source
            .last_successful_scan_at
            .is_not(None),

            Source.last_successful_scan_at
            == Source.last_scanned_at,
        )

        source_counts = db.execute(
            select(
                count_when(
                    scanned_today_condition
                ).label(
                    "scanned_today"
                ),

                count_when(
                    and_(
                        Source
                        .first_discovered_at
                        >= start_of_today,

                        Source
                        .first_discovered_at
                        < start_of_tomorrow,
                    )
                ).label(
                    "discovered_today"
                ),

                count_when(
                    latest_scan_succeeded
                ).label(
                    "successful_today"
                ),
            )
        ).one()

    sources_scanned_today = int(
        source_counts.scanned_today
        or 0
    )

    successful_sources_today = int(
        source_counts.successful_today
        or 0
    )

    scan_success_rate = (
        round(
            successful_sources_today
            / sources_scanned_today
            * 100,
            1,
        )
        if sources_scanned_today
        else 0.0
    )

    return {
        "new_today": int(
            opportunity_counts.new_today
            or 0
        ),

        "new_this_week": int(
            opportunity_counts.new_this_week
            or 0
        ),

        "active": int(
            opportunity_counts.active
            or 0
        ),

        "expiring_soon": int(
            opportunity_counts.expiring_soon
            or 0
        ),

        "expired": int(
            opportunity_counts.expired
            or 0
        ),

        "archived": int(
            opportunity_counts.archived
            or 0
        ),

        "pipeline": int(
            opportunity_counts.pipeline
            or 0
        ),

        "sources_scanned_today": (
            sources_scanned_today
        ),

        "sources_discovered_today": int(
            source_counts.discovered_today
            or 0
        ),

        "scan_success_rate": (
            scan_success_rate
        ),
    }


def get_status_counts(
    inbox_condition: ColumnElement[bool],
) -> dict[str, int]:
    counts = {
        status: 0
        for status
        in OPPORTUNITY_STATUSES
    }

    with SessionLocal() as db:
        results = db.execute(
            select(
                pursuit_column("status"),
                func.count(
                    Opportunity.id
                ),
            )
            .where(
                inbox_condition
            )
            .group_by(
                pursuit_column("status")
            )
        ).all()

    for status, count in results:
        if status:
            counts[str(status)] = int(cast(int, count))

    return counts


def get_categories(
    inbox_condition: ColumnElement[bool],
) -> list[str]:
    with SessionLocal() as db:
        categories = db.scalars(
            select(
                Opportunity.category
            )
            .where(
                inbox_condition
            )
            .distinct()
            .order_by(
                Opportunity.category
            )
        ).all()

    return [
        category
        for category in categories
        if category
    ]


def get_active_business_profile() -> tuple[
    Organization | None,
    OpportunityPreference | None,
]:
    """
    Return the active development workspace
    and its saved opportunity preferences.

    Authentication will later replace the
    'first active organization' lookup.
    """

    with SessionLocal() as db:
        organization = db.scalar(
            select(
                Organization
            )
            .where(
                Organization
                .is_active
                .is_(True)
            )
            .order_by(
                Organization
                .created_at
                .asc()
            )
            .limit(1)
        )

        if organization is None:
            return (
                None,
                None,
            )

        preference = db.scalar(
            select(
                OpportunityPreference
            )
            .where(
                OpportunityPreference
                .organization_id
                == organization.id
            )
        )

        return (
            organization,
            preference,
        )


def build_business_match_preferences(
    preference: OpportunityPreference | None,
) -> BusinessMatchPreferences | None:
    """
    Convert the persisted ORM preference object
    into the side-effect-free matching structure.
    """

    if preference is None:
        return None

    return BusinessMatchPreferences(
        countries=tuple(
            preference.countries
            or []
        ),

        regions=tuple(
            preference.regions
            or []
        ),

        industries=tuple(
            preference.industries
            or []
        ),

        services=tuple(
            preference.services
            or []
        ),

        opportunity_types=tuple(
            preference.opportunity_types
            or []
        ),

        keywords=tuple(
            preference.keywords
            or []
        ),

        languages=tuple(
            preference.languages
            or []
        ),

        minimum_match_score=(
            preference.minimum_match_score
            or 0
        ),

        include_no_deadline=(
            preference.include_no_deadline
        ),

        include_jobs=(
            preference.include_jobs
        ),

        include_tenders=(
            preference.include_tenders
        ),

        include_grants=(
            preference.include_grants
        ),

        include_partnerships=(
            preference.include_partnerships
        ),
    )


def opportunity_to_filtered(
    opportunity: Opportunity,
) -> FilteredOpportunity:
    """
    Convert a stored global Opportunity into
    the classifier DTO used by business matching.
    """

    return FilteredOpportunity(
        organisation_name=(
            opportunity.organisation_name
        ),

        title=(
            opportunity.title
        ),

        category=(
            opportunity.category
        ),

        source_name=(
            opportunity.source_name
        ),

        source_url=(
            opportunity.source_url
        ),

        match_score=(
            opportunity.match_score
            or 0
        ),

        match_reason=(
            opportunity.match_reason
            or ""
        ),

        description=(
            opportunity.description
        ),

        deadline=(
            opportunity.deadline
        ),
    )


def personalize_opportunities(
    opportunities: list[Opportunity],
    preferences: BusinessMatchPreferences | None,
) -> list[Opportunity]:
    """
    Apply business-specific scoring in memory.

    Global opportunity records are not modified
    in the database.
    """

    visible: list[Opportunity] = []

    for opportunity in opportunities:
        if preferences is None:
            setattr(
                opportunity,
                "display_match_score",
                opportunity.match_score or 0,
            )

            setattr(
                opportunity,
                "display_match_reason",
                opportunity.match_reason or "",
            )

            visible.append(
                opportunity
            )

            continue

        result = score_for_business(
            opportunity_to_filtered(
                opportunity
            ),
            preferences,
        )

        setattr(
            opportunity,
            "display_match_score",
            result.score,
        )

        setattr(
            opportunity,
            "display_match_reason",
            result.reason,
        )

        if result.is_visible:
            visible.append(
                opportunity
            )

    return visible


def opportunity_sort_key(
    opportunity: Opportunity,
):
    """
    Priority, match strength/score, approaching deadline, freshness, then ID.
    Untriaged results follow MEDIUM and precede LOW; no AI is invoked here.
    """

    score = getattr(
        opportunity,
        "display_match_score",
        opportunity.match_score or 0,
    )

    discovered = (
        opportunity.first_discovered_at
        or datetime.min.replace(
            tzinfo=timezone.utc
        )
    )

    deadline = (
        opportunity.deadline
        or date.max
    )

    triage = getattr(opportunity, "display_triage", None)
    strength_order = {"VERY_STRONG": 0, "STRONG": 1, "MODERATE": 2, "WEAK": 3, "NOT_A_MATCH": 4}
    return (
        PRIORITY_ORDER.get(triage.priority.value, 3) if triage else 3,
        strength_order.get(getattr(opportunity, "display_match_strength", "") or "", 5),
        -score,
        deadline,
        -discovered.timestamp(),
        opportunity.id,
    )


def apply_stored_company_match(
    opportunity: Opportunity,
    match: CompanyOpportunityMatch,
    organization: Organization,
) -> Opportunity:
    """Attach read-only stored match fields used by the templates."""
    from app.intelligence.applicant_evidence import capability_only

    setattr(opportunity, "display_match_score", match.score)
    setattr(opportunity, "display_applicant_eligibility", match.applicant_eligibility or "UNKNOWN")
    setattr(opportunity, "display_match_reason", capability_only(match.explanation or ""))
    setattr(opportunity, "display_match_strength", match.match_strength)
    setattr(opportunity, "display_match_strengths", [cleaned for item in match.strengths if (cleaned := capability_only(item))])
    setattr(opportunity, "display_match_gaps", [cleaned for item in match.potential_gaps if (cleaned := capability_only(item))])
    setattr(
        opportunity,
        "display_eligibility_concerns",
        [cleaned for item in match.eligibility_concerns if (cleaned := capability_only(item))],
    )
    setattr(
        opportunity,
        "display_match_recommendation",
        capability_only(match.recommendation or ""),
    )
    setattr(opportunity, "display_match_company_id", organization.id)
    setattr(opportunity, "display_match_company_name", organization.name)
    setattr(opportunity, "display_match_minimum_score", match.minimum_score)
    return opportunity


def matched_opportunities_from_rows(
    rows: list[tuple[Opportunity, CompanyOpportunityMatch, Organization]],
) -> list[Opportunity]:
    """Return one opportunity per ID, retaining its strongest stored match."""
    opportunities: list[Opportunity] = []
    seen: set[int] = set()
    for opportunity, match, organization in rows:
        if opportunity.id in seen:
            continue
        seen.add(opportunity.id)
        opportunities.append(
            apply_stored_company_match(opportunity, match, organization)
        )
    return opportunities


def load_opportunity_analyses(
    db: Session,
    opportunity_ids: set[int],
) -> dict[int, OpportunityAnalysis]:
    """Load one durable AI analysis per opportunity in a single query."""
    from app.intelligence.opportunity_pipeline import load_canonical_analyses

    return load_canonical_analyses(db, opportunity_ids)


def apply_stored_opportunity_analysis(
    opportunity: Opportunity,
    analysis: OpportunityAnalysis | None,
) -> Opportunity:
    """Attach concise stored intelligence without triggering analysis."""
    setattr(opportunity, "ai_title", analysis.title if analysis else None)
    from app.intelligence.eligibility import applicant_eligibility_statement, applicant_eligibility_state

    setattr(opportunity, "ai_applicant_eligibility_note", applicant_eligibility_statement(analysis) if analysis else "Applicant eligibility requires confirmation; no structured analysis is available.")
    # Legacy stored match prose/state cannot override the current evidence contract.
    setattr(opportunity, "display_applicant_eligibility", applicant_eligibility_state(analysis).value if analysis else "UNKNOWN")
    setattr(
        opportunity,
        "ai_buyer",
        (
            analysis.buyer_name or analysis.issuing_organisation
            if analysis
            else opportunity.organisation_name
        ),
    )
    setattr(opportunity, "ai_country", analysis.country if analysis else None)
    delivery = list(analysis.delivery_countries) if analysis else []
    scope = analysis.geographic_scope.value if analysis else "UNKNOWN"
    setattr(opportunity, "ai_buyer_country", analysis.buyer_country if analysis else None)
    setattr(opportunity, "ai_delivery_countries", delivery)
    setattr(opportunity, "ai_eligibility_countries", list(analysis.eligibility_countries) if analysis else [])
    setattr(opportunity, "ai_geographic_scope", scope.replace("_", " ").title())
    setattr(opportunity, "ai_geography", " · ".join(delivery) if delivery else scope.replace("_", " ").title())
    setattr(opportunity, "ai_location", analysis.location if analysis else None)
    setattr(
        opportunity,
        "ai_opportunity_type",
        analysis.opportunity_type.value if analysis else opportunity.category,
    )
    setattr(opportunity, "ai_summary", analysis.summary if analysis else None)
    setattr(opportunity, "ai_scope", analysis.scope if analysis else None)
    setattr(
        opportunity,
        "ai_services_required",
        list(analysis.services_required) if analysis else [],
    )
    setattr(
        opportunity,
        "ai_deliverables",
        list(analysis.deliverables) if analysis else [],
    )
    setattr(
        opportunity,
        "ai_experience_requirements",
        list(analysis.experience_requirements) if analysis else [],
    )
    setattr(
        opportunity,
        "ai_eligibility_requirements",
        list(analysis.eligibility_requirements) if analysis else [],
    )
    setattr(
        opportunity,
        "ai_qualification_requirements",
        list(analysis.qualification_requirements) if analysis else [],
    )
    setattr(
        opportunity,
        "ai_required_documents",
        list(analysis.required_documents) if analysis else [],
    )
    setattr(
        opportunity,
        "ai_submission_method",
        analysis.submission_method if analysis else None,
    )
    setattr(
        opportunity,
        "ai_submission_address",
        analysis.submission_address if analysis else None,
    )
    setattr(
        opportunity,
        "ai_estimated_value",
        analysis.estimated_value if analysis else None,
    )
    setattr(opportunity, "ai_currency", analysis.currency if analysis else None)
    return opportunity


def personalize_dashboard_metrics(
    dashboard_metrics: dict[
        str,
        int | float,
    ],
    opportunities: list[Opportunity],
    now: datetime,
) -> dict[str, int | float]:
    """
    Replace inbox-related counts with counts from the
    personalized opportunity feed.

    Scanner/source statistics remain operational/global.
    """

    today = now.date()

    start_of_week = (
        today
        - timedelta(
            days=today.weekday()
        )
    )

    expiring_before = (
        today
        + timedelta(days=7)
    )

    active = [
        opportunity
        for opportunity
        in opportunities
        if (
            not opportunity.is_expired
            and (
                opportunity.deadline is None
                or opportunity.deadline
                >= today
            )
        )
    ]

    dashboard_metrics[
        "active"
    ] = len(
        active
    )

    dashboard_metrics[
        "new_today"
    ] = sum(
        1
        for opportunity
        in opportunities
        if (
            opportunity.first_discovered_at is not None
            and opportunity
            .first_discovered_at
            .date()
            == today
        )
    )

    dashboard_metrics[
        "new_this_week"
    ] = sum(
        1
        for opportunity
        in opportunities
        if (
            opportunity.first_discovered_at is not None
            and start_of_week
            <= opportunity
            .first_discovered_at
            .date()
            <= today
        )
    )

    dashboard_metrics[
        "expiring_soon"
    ] = sum(
        1
        for opportunity
        in opportunities
        if (
            not opportunity.is_expired
            and opportunity.deadline
            is not None
            and today
            <= opportunity.deadline
            <= expiring_before
        )
    )

    return dashboard_metrics


@app.get("/")
def home(
    request: Request,

    q: Annotated[
        str | None,
        Query(),
    ] = None,

    category: Annotated[
        str | None,
        Query(),
    ] = None,

    status: Annotated[
        str | None,
        Query(),
    ] = None,

    page: Annotated[
        int,
        Query(ge=1),
    ] = 1,

    deadline_filter: Annotated[
        str | None,
        Query(),
    ] = None,

    company_id: Annotated[
        int | None,
        Query(ge=1),
    ] = None,

    country: Annotated[str | None, Query()] = None,

    match_strength: Annotated[str | None, Query()] = None,
):
    now = current_utc_time()
    today = now.date()

    inbox_condition = (
        opportunity_inbox_condition(
            now
        )
    )

    archive_condition = (
        opportunity_archive_condition(
            now
        )
    )

    statement = (
        select(
            Opportunity,
            CompanyOpportunityMatch,
            Organization,
        )
        .join(
            CompanyOpportunityMatch,
            CompanyOpportunityMatch.opportunity_id == Opportunity.id,
        )
        .join(
            Organization,
            Organization.id == CompanyOpportunityMatch.organization_id,
        )
        .where(
            CompanyOpportunityMatch.qualifies_for_feed.is_(True),
            CompanyOpportunityMatch.error_reason.is_(None),
            Organization.is_active.is_(True),
        )
    )

    if company_id is not None:
        statement = statement.where(Organization.id == company_id)
    if match_strength:
        statement = statement.where(
            CompanyOpportunityMatch.match_strength == match_strength.upper()
        )

    if deadline_filter == "archived":
        statement = statement.where(
            archive_condition
        )

    elif deadline_filter == "expired":
        statement = statement.where(
            or_(
                pursuit_expired()
                .is_(True),

                and_(
                    Opportunity
                    .deadline
                    .is_not(None),

                    Opportunity.deadline
                    < today,
                ),
            )
        )

    else:
        statement = statement.where(
            inbox_condition
        )

    if q:
        search_term = (
            f"%{q.strip()}%"
        )

        statement = statement.where(
            or_(
                Opportunity.title.ilike(
                    search_term
                ),

                Opportunity
                .organisation_name
                .ilike(
                    search_term
                ),

                Opportunity
                .description
                .ilike(
                    search_term
                ),

                Opportunity
                .category
                .ilike(
                    search_term
                ),
            )
        )

    if category:
        statement = statement.where(
            Opportunity.category
            == category
        )

    if status:
        statement = statement.where(
            pursuit_column("status")
            == status
        )

    if deadline_filter == "open":
        statement = statement.where(
            pursuit_expired().is_(
                False
            ),

            or_(
                Opportunity.deadline
                >= today,

                Opportunity.deadline
                .is_(None),
            ),
        )

    elif deadline_filter == "no_deadline":
        statement = statement.where(
            Opportunity.deadline.is_(
                None
            )
        )

    elif deadline_filter == "due_7_days":
        seven_days_from_now = (
            today
            + timedelta(days=7)
        )

        statement = statement.where(
            Opportunity.deadline.is_not(
                None
            ),
            Opportunity.deadline
            >= today,
            Opportunity.deadline
            <= seven_days_from_now,
        )

    elif deadline_filter == "due_30_days":
        thirty_days_from_now = (
            today
            + timedelta(days=30)
        )

        statement = statement.where(
            Opportunity.deadline.is_not(
                None
            ),
            Opportunity.deadline
            >= today,
            Opportunity.deadline
            <= thirty_days_from_now,
        )

    with SessionLocal() as db:
        active_companies = list(
            db.scalars(
                select(Organization)
                .where(Organization.is_active.is_(True))
                .order_by(Organization.name.asc(), Organization.id.asc())
            ).all()
        )
        selected_company = next(
            (
                organization
                for organization in active_companies
                if organization.id == company_id
            ),
            None,
        )
        if company_id is not None and selected_company is None:
            raise HTTPException(status_code=404, detail="Company not found")

        candidate_rows = [
            (opportunity, match, organization)
            for opportunity, match, organization in db.execute(
                statement.order_by(CompanyOpportunityMatch.score.desc(), Organization.id.asc())
            )
        ]

        inbox_statement = (
            select(
                Opportunity,
                CompanyOpportunityMatch,
                Organization,
            )
            .join(
                CompanyOpportunityMatch,
                CompanyOpportunityMatch.opportunity_id == Opportunity.id,
            )
            .join(
                Organization,
                Organization.id == CompanyOpportunityMatch.organization_id,
            )
            .where(
                inbox_condition,
                CompanyOpportunityMatch.qualifies_for_feed.is_(True),
                CompanyOpportunityMatch.error_reason.is_(None),
                Organization.is_active.is_(True),
            )
        )
        if company_id is not None:
            inbox_statement = inbox_statement.where(
                Organization.id == company_id
            )
        if match_strength:
            inbox_statement = inbox_statement.where(
                CompanyOpportunityMatch.match_strength == match_strength.upper()
            )
        inbox_rows = [
            (opportunity, match, organization)
            for opportunity, match, organization in db.execute(
                inbox_statement.order_by(CompanyOpportunityMatch.score.desc(), Organization.id.asc())
            )
        ]
        analysis_by_opportunity = load_opportunity_analyses(
            db,
            {
                opportunity.id
                for opportunity, _, _ in candidate_rows + inbox_rows
            },
        )
        triage_by_pair = load_stored_triage(
            db, {opportunity.id for opportunity, _, _ in candidate_rows + inbox_rows},
        )
        profiles_by_company = load_current_profiles(db)
        # Filter before strongest-match selection, counts, ordering or pagination.
        candidate_rows = [
            row for row in candidate_rows
            if match_is_current(row[1], profile=profiles_by_company.get(row[2].id), analysis=analysis_by_opportunity.get(row[0].id))
        ]
        inbox_rows = [
            row for row in inbox_rows
            if match_is_current(row[1], profile=profiles_by_company.get(row[2].id), analysis=analysis_by_opportunity.get(row[0].id))
        ]

    candidate_opportunities = matched_opportunities_from_rows(candidate_rows)
    inbox_candidates = matched_opportunities_from_rows(inbox_rows)
    matches_by_pair = {
        (opportunity.id, company.id): match
        for opportunity, match, company in candidate_rows + inbox_rows
    }
    for opportunity in candidate_opportunities + inbox_candidates:
        pair = (opportunity.id, getattr(opportunity, "display_match_company_id"))
        setattr(opportunity, "display_triage", stored_triage_result(
            opportunity, matches_by_pair[pair], triage_by_pair.get(pair),
            profile=profiles_by_company.get(pair[1]),
            analysis=analysis_by_opportunity.get(opportunity.id),
        ))
        apply_stored_opportunity_analysis(
            opportunity,
            analysis_by_opportunity.get(opportunity.id),
        )

    available_countries = sorted(
        {
            country_name
            for opportunity in candidate_opportunities
            for country_name in getattr(opportunity, "ai_delivery_countries", [])
        }
    )
    if country:
        selected_country = country.casefold()
        candidate_opportunities = [
            opportunity
            for opportunity in candidate_opportunities
            if selected_country in {value.casefold() for value in getattr(opportunity, "ai_delivery_countries", [])}
        ]
        inbox_candidates = [
            opportunity
            for opportunity in inbox_candidates
            if selected_country in {value.casefold() for value in getattr(opportunity, "ai_delivery_countries", [])}
        ]

    candidate_opportunities.sort(
        key=opportunity_sort_key
    )

    filtered_total = len(
        candidate_opportunities
    )

    total_opportunities = len(
        inbox_candidates
    )

    total_pages = max(
        1,
        (
            filtered_total
            + DASHBOARD_PAGE_SIZE
            - 1
        )
        // DASHBOARD_PAGE_SIZE,
    )

    current_page = min(
        page,
        total_pages,
    )

    start_index = (
        current_page - 1
    ) * DASHBOARD_PAGE_SIZE

    end_index = (
        start_index
        + DASHBOARD_PAGE_SIZE
    )

    opportunities = (
        candidate_opportunities[
            start_index:end_index
        ]
    )

    with SessionLocal() as db:
        readiness_by_pair = load_stored_readiness(db, {item.id for item in opportunities})
    for opportunity in opportunities:
        readiness_view = readiness_by_pair.get((opportunity.id, getattr(opportunity, "display_match_company_id")))
        setattr(opportunity, "display_readiness", readiness_view.summary if readiness_view else None)

    dashboard_metrics = (
        get_dashboard_metrics(
            now
        )
    )

    dashboard_metrics = (
        personalize_dashboard_metrics(
            dashboard_metrics,
            inbox_candidates,
            now,
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "opportunities": (
                opportunities
            ),

            "filtered_count": len(
                opportunities
            ),

            "filtered_total": (
                filtered_total
            ),

            "total_opportunities": (
                total_opportunities
            ),

            "page": (
                current_page
            ),

            "page_size": (
                DASHBOARD_PAGE_SIZE
            ),

            "total_pages": (
                total_pages
            ),

            "dashboard_metrics": (
                dashboard_metrics
            ),

            "categories": (
                get_categories(
                    inbox_condition
                )
            ),

            "statuses": (
                OPPORTUNITY_STATUSES
            ),

            "today": (
                today
            ),

            "selected_q": (
                q or ""
            ),

            "selected_category": (
                category or ""
            ),

            "selected_status": (
                status or ""
            ),

            "selected_deadline": (
                deadline_filter or ""
            ),

            "selected_country": country or "",
            "selected_match_strength": match_strength or "",
            "countries": available_countries,
            "match_strengths": (
                "VERY_STRONG",
                "STRONG",
                "MODERATE",
                "WEAK",
            ),

            "inbox_no_deadline_max_age_days": (
                get_settings()
                .inbox_no_deadline_max_age_days
            ),

            "business_profile_active": (
                bool(active_companies)
            ),

            "business_profile_name": (
                selected_company.name
                if selected_company is not None
                else "All companies"
            ),

            "business_minimum_match_score": (
                getattr(opportunities[0], "display_match_minimum_score", 0)
                if selected_company is not None and opportunities
                else 0
            ),

            "companies": active_companies,
            "selected_company_id": company_id,
            "strong_match_count": sum(
                getattr(opportunity, "display_match_score", 0) >= 80
                for opportunity in inbox_candidates
            ),
        },
    )


@app.get(
    "/opportunities/{opportunity_id}"
)
def opportunity_detail(
    opportunity_id: int,
    request: Request,
    company_id: Annotated[
        int | None,
        Query(ge=1),
    ] = None,
):
    with SessionLocal() as db:
        opportunity = db.get(Opportunity, opportunity_id)

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity not found",
            )

        if opportunity.status == "New" and principal.get() is None:
            opportunity.status = "Seen"
            db.commit()
            db.refresh(opportunity)

        companies = list(
            db.scalars(
                select(Organization)
                .where(Organization.is_active.is_(True))
                .order_by(Organization.name.asc(), Organization.id.asc())
            ).all()
        )
        selected_company = next(
            (
                organization
                for organization in companies
                if organization.id == company_id
            ),
            None,
        )
        if company_id is not None and selected_company is None:
            raise HTTPException(status_code=404, detail="Company not found")

        match_statement = (
            select(CompanyOpportunityMatch, Organization)
            .join(
                Organization,
                Organization.id == CompanyOpportunityMatch.organization_id,
            )
            .where(
                CompanyOpportunityMatch.opportunity_id == opportunity_id,
                CompanyOpportunityMatch.error_reason.is_(None),
                Organization.is_active.is_(True),
            )
        )
        if company_id is not None:
            match_statement = match_statement.where(
                Organization.id == company_id
            )
        else:
            match_statement = match_statement.where(
                CompanyOpportunityMatch.qualifies_for_feed.is_(True)
            )

        stored_match_rows = db.execute(
            match_statement.order_by(CompanyOpportunityMatch.score.desc(), Organization.id.asc())
        ).all()
        analysis_by_opportunity = load_opportunity_analyses(db, {opportunity_id})
        profiles_by_company = load_current_profiles(db)
        stored_match_row = next((
            row for row in stored_match_rows
            if match_is_current(row[0], profile=profiles_by_company.get(row[1].id), analysis=analysis_by_opportunity.get(opportunity_id))
        ), None)
        match_needs_refresh = bool(stored_match_rows) and stored_match_row is None
        stored_match = None
        match_company = selected_company
        if stored_match_row is not None:
            stored_match, match_company = stored_match_row
            apply_stored_company_match(
                opportunity,
                stored_match,
                match_company,
            )
            triage_by_pair = load_stored_triage(db, {opportunity_id})
            setattr(opportunity, "display_triage", stored_triage_result(
                opportunity, stored_match,
                triage_by_pair.get((opportunity_id, stored_match.organization_id)),
                profile=profiles_by_company.get(stored_match.organization_id),
                analysis=analysis_by_opportunity.get(opportunity_id),
            ))
        apply_stored_opportunity_analysis(
            opportunity,
            analysis_by_opportunity.get(opportunity_id),
        )
        from app.intelligence.provenance import public_url

        discovered_urls = list(dict.fromkeys(
            url for value in [opportunity.source_url, *db.scalars(
                select(AIOpportunityAnalysis.source_url).where(AIOpportunityAnalysis.opportunity_id == opportunity_id)
            )] if (url := public_url(value))
        ))
        setattr(opportunity, "display_original_source", public_url(opportunity.original_source_url))
        setattr(opportunity, "display_discovered_urls", [url for url in discovered_urls if url != public_url(opportunity.original_source_url)])

        readiness = None
        if match_company is not None:
            readiness = load_stored_readiness(
                db, {opportunity_id}, company_id=match_company.id, include_items=True,
            ).get((opportunity_id, match_company.id))

    business_match_visible = bool(
        stored_match is not None and stored_match.qualifies_for_feed
    )

    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "opportunity": opportunity,
            "statuses": OPPORTUNITY_STATUSES,
            "lead_statuses": LEAD_STATUSES,
            "lead_priorities": LEAD_PRIORITIES,
            "today": date.today(),
            "business_profile_active": match_company is not None,
            "business_profile_name": (
                match_company.name if match_company else None
            ),
            "stored_match": stored_match,
            "match_needs_refresh": match_needs_refresh,
            "readiness": readiness,
            "readiness_company": match_company,
            "companies": companies,
            "selected_company_id": company_id,
            "business_match_visible": business_match_visible,
            "business_minimum_match_score": (
                stored_match.minimum_score
                if stored_match is not None
                else 0
            ),
        },
    )


@app.post(
    "/opportunities/{opportunity_id}/update"
)
def update_opportunity(
    opportunity_id: int,

    status: Annotated[
        str,
        Form(),
    ],

    user_notes: Annotated[
        str,
        Form(),
    ] = "",
):
    if status not in (
        OPPORTUNITY_STATUSES
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid opportunity status"
            ),
        )

    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Opportunity not found"
                ),
            )

        opportunity = private_state(db, opportunity)

        opportunity.status = status

        opportunity.user_notes = (
            user_notes.strip()
            or None
        )

        if status == "Expired":
            opportunity.is_expired = (
                True
            )

        elif (
            opportunity.deadline
            is None
            or opportunity.deadline
            >= date.today()
        ):
            opportunity.is_expired = (
                False
            )

        db.commit()

    return RedirectResponse(
        url=(
            f"/opportunities/"
            f"{opportunity_id}"
        ),
        status_code=303,
    )


@app.post(
    "/opportunities/{opportunity_id}/status"
)
def quick_update_status(
    opportunity_id: int,

    status: Annotated[
        str,
        Form(),
    ],
):
    if status not in (
        OPPORTUNITY_STATUSES
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid opportunity status"
            ),
        )

    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Opportunity not found"
                ),
            )

        opportunity = private_state(db, opportunity)

        opportunity.status = (
            status
        )

        if status == "Expired":
            opportunity.is_expired = (
                True
            )

        elif (
            opportunity.deadline
            is None
            or opportunity.deadline
            >= date.today()
        ):
            opportunity.is_expired = (
                False
            )

        db.commit()

    return RedirectResponse(
        url="/",
        status_code=303,
    )


@app.post(
    "/opportunities/{opportunity_id}/pipeline/add"
)
def add_to_pipeline(
    opportunity_id: int,
):
    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Opportunity not found"
                ),
            )

        opportunity = private_state(db, opportunity)

        opportunity.is_lead = (
            True
        )

        opportunity.status = (
            "Interested"
        )

        if not opportunity.lead_status:
            opportunity.lead_status = (
                "Under Review"
            )

        if not opportunity.lead_priority:
            opportunity.lead_priority = (
                "Medium"
            )

        db.commit()

    return RedirectResponse(
        url=(
            f"/opportunities/"
            f"{opportunity_id}"
        ),
        status_code=303,
    )


@app.post(
    "/opportunities/{opportunity_id}/pipeline/remove"
)
def remove_from_pipeline(
    opportunity_id: int,
):
    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Opportunity not found"
                ),
            )

        opportunity = private_state(db, opportunity)

        opportunity.is_lead = (
            False
        )

        db.commit()

    return RedirectResponse(
        url="/pipeline",
        status_code=303,
    )


@app.post("/opportunities/{opportunity_id}/readiness/analyze")
def analyze_readiness_action(
    opportunity_id: int,
    company_id: Annotated[int, Form(ge=1)],
):
    """User-requested operation only; GET routes never generate readiness."""
    with SessionLocal() as db:
        company = db.get(Organization, company_id)
        if company is None or not company.is_active:
            raise HTTPException(status_code=404, detail="Company not found")
        if db.get(Opportunity, opportunity_id) is None:
            raise HTTPException(status_code=404, detail="Opportunity not found")
    try:
        outcomes = analyze_opportunity_readiness(opportunity_id, company_ids=[company_id])
        result = "error" if any(item.status == "ERROR" for item in outcomes) else "saved"
    except ValueError:
        raise HTTPException(status_code=400, detail="Readiness requires a verified opportunity and active company profile") from None
    except Exception:
        result = "error"
    return RedirectResponse(
        url=f"/opportunities/{opportunity_id}?company_id={company_id}&readiness_result={result}",
        status_code=303,
    )


@app.get("/pipeline")
def pipeline_page(
    request: Request,
):
    today = date.today()

    with SessionLocal() as db:
        opportunities = db.scalars(
            select(
                Opportunity
            )
            .where(
                pursuit_column("is_lead")
                .is_(True)
            )
            .order_by(
                Opportunity
                .deadline
                .is_(None),

                Opportunity
                .deadline
                .asc(),

                Opportunity
                .first_discovered_at
                .desc(),
            )
        ).all()

        readiness_by_pair = load_stored_readiness(db, {item.id for item in opportunities})
        preparation_by_opportunity: dict[int, list] = {}
        for (item_id, _), view in readiness_by_pair.items():
            preparation_by_opportunity.setdefault(item_id, []).append(view)
        for opportunity in opportunities:
            setattr(opportunity, "preparation_summaries", preparation_by_opportunity.get(opportunity.id, []))

    overdue_count = sum(
        1
        for opportunity
        in opportunities
        if (
            opportunity.next_action_due
            and opportunity.next_action_due
            < today
        )
    )

    unassigned_count = sum(
        1
        for opportunity
        in opportunities
        if not opportunity.assigned_to
    )

    high_priority_count = sum(
        1
        for opportunity
        in opportunities
        if (
            opportunity.lead_priority
            and opportunity
            .lead_priority
            .casefold()
            == "high"
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="pipeline.html",
        context={
            "opportunities": (
                opportunities
            ),

            "pipeline_count": len(
                opportunities
            ),

            "overdue_count": (
                overdue_count
            ),

            "unassigned_count": (
                unassigned_count
            ),

            "high_priority_count": (
                high_priority_count
            ),

            "today": (
                today
            ),

            "lead_statuses": (
                LEAD_STATUSES
            ),

            "lead_priorities": (
                LEAD_PRIORITIES
            ),
        },
    )


@app.post(
    "/opportunities/{opportunity_id}/pipeline/update"
)
def update_pipeline_opportunity(
    opportunity_id: int,

    assigned_to: Annotated[
        str,
        Form(),
    ] = "",

    lead_priority: Annotated[
        str,
        Form(),
    ] = "",

    lead_status: Annotated[
        str,
        Form(),
    ] = "",

    next_action: Annotated[
        str,
        Form(),
    ] = "",

    next_action_due: Annotated[
        str,
        Form(),
    ] = "",

    last_follow_up_date: Annotated[
        str,
        Form(),
    ] = "",

    lead_notes: Annotated[
        str,
        Form(),
    ] = "",
):
    if (
        lead_priority
        and lead_priority
        not in LEAD_PRIORITIES
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid lead priority"
            ),
        )

    if (
        lead_status
        and lead_status
        not in LEAD_STATUSES
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid lead status"
            ),
        )

    try:
        parsed_next_action_due = (
            date.fromisoformat(
                next_action_due
            )
            if next_action_due
            else None
        )

        parsed_last_follow_up_date = (
            date.fromisoformat(
                last_follow_up_date
            )
            if last_follow_up_date
            else None
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid date",
        ) from exc

    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Opportunity not found"
                ),
            )

        opportunity = private_state(db, opportunity)

        if not opportunity.is_lead:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Opportunity is not "
                    "in the pipeline"
                ),
            )

        opportunity.assigned_to = (
            assigned_to.strip()
            or None
        )

        opportunity.lead_priority = (
            lead_priority
            or None
        )

        opportunity.lead_status = (
            lead_status
            or None
        )

        opportunity.next_action = (
            next_action.strip()
            or None
        )

        opportunity.next_action_due = (
            parsed_next_action_due
        )

        opportunity.last_follow_up_date = (
            parsed_last_follow_up_date
        )

        opportunity.lead_notes = (
            lead_notes.strip()
            or None
        )

        if lead_status == "Submitted":
            opportunity.status = (
                "Applied"
            )

        elif lead_status in {
            "Awarded",
            "Pursuing",
            "Under Review",
            "On Hold",
        }:
            opportunity.status = (
                "Interested"
            )

        elif lead_status in {
            "Lost",
            "Cancelled",
        }:
            opportunity.status = (
                "Rejected"
            )

        db.commit()

    return RedirectResponse(
        url="/pipeline",
        status_code=303,
    )


@app.get("/business-profile")
def business_profile_page(
    request: Request,
    company_id: Annotated[int | None, Query(ge=1)] = None,
    new: Annotated[bool, Query()] = False,
):
    if new and company_id is not None:
        raise HTTPException(status_code=400, detail="Choose a new or existing company, not both")
    with SessionLocal() as db:
        companies = list(db.scalars(
            select(Organization).where(Organization.is_active.is_(True))
            .order_by(Organization.created_at.asc(), Organization.id.asc())
        ).all())
        organization = None if new else db.scalar(
            select(
                Organization
            )
            .where(
                Organization
                .is_active
                .is_(True)
            )
            .where(Organization.id == company_id if company_id else true())
            .order_by(
                Organization
                .created_at
                .asc()
            )
            .limit(1)
        )

        preference = None

        if company_id is not None and organization is None:
            raise HTTPException(status_code=404, detail="Company not found")

        if organization is not None:
            preference = db.scalar(
                select(
                    OpportunityPreference
                )
                .where(
                    OpportunityPreference
                    .organization_id
                    == organization.id
                )
            )

    return templates.TemplateResponse(
        request=request,
        name="business_profile.html",
        context={
            "companies": companies,
            "create_new": new or organization is None,
            "intelligence": CompanyIntelligence.model_validate(
                (preference.intelligence or {}) if preference else {}
            ).model_dump(),
            "intelligence_sections": INTELLIGENCE_SECTIONS,
            "organization": (
                organization
            ),

            "preference": (
                preference
            ),
        },
    )


@app.post("/business-profile")
def save_business_profile(
    name: Annotated[
        str,
        Form(),
    ],

    country: Annotated[
        str,
        Form(),
    ] = "",

    city: Annotated[
        str,
        Form(),
    ] = "",

    website: Annotated[
        str,
        Form(),
    ] = "",

    industry: Annotated[
        str,
        Form(),
    ] = "",

    description: Annotated[
        str,
        Form(),
    ] = "",

    default_language: Annotated[
        str,
        Form(),
    ] = "en",

    timezone_name: Annotated[
        str,
        Form(
            alias="timezone"
        ),
    ] = "",

    countries: Annotated[
        str,
        Form(),
    ] = "",

    regions: Annotated[
        str,
        Form(),
    ] = "",

    industries: Annotated[
        str,
        Form(),
    ] = "",

    services: Annotated[
        str,
        Form(),
    ] = "",

    opportunity_types: Annotated[
        str,
        Form(),
    ] = "",

    keywords: Annotated[
        str,
        Form(),
    ] = "",

    languages: Annotated[
        str,
        Form(),
    ] = "",

    minimum_match_score: Annotated[
        int,
        Form(),
    ] = 0,

    include_no_deadline: Annotated[
        str | None,
        Form(),
    ] = None,

    include_jobs: Annotated[
        str | None,
        Form(),
    ] = None,

    include_tenders: Annotated[
        str | None,
        Form(),
    ] = None,

    include_grants: Annotated[
        str | None,
        Form(),
    ] = None,

    include_partnerships: Annotated[
        str | None,
        Form(),
    ] = None,
    organization_id: Annotated[int | None, Form(ge=1)] = None,
    create_new: Annotated[bool, Form()] = False,
    rich_profile: Annotated[bool, Form()] = False,
    capabilities: Annotated[str | None, Form()] = None,
    areas_of_expertise: Annotated[str | None, Form()] = None,
    assignment_types: Annotated[str | None, Form()] = None,
    client_types: Annotated[str | None, Form()] = None,
    sector_experience: Annotated[str | None, Form()] = None,
    relevant_experience: Annotated[str | None, Form()] = None,
    track_record: Annotated[str | None, Form()] = None,
    preferred_sectors: Annotated[str | None, Form()] = None,
    excluded_services: Annotated[str | None, Form()] = None,
    excluded_sectors: Annotated[str | None, Form()] = None,
    excluded_opportunity_types: Annotated[str | None, Form()] = None,
    excluded_countries: Annotated[str | None, Form()] = None,
):
    if create_new and organization_id is not None:
        raise HTTPException(status_code=400, detail="A new company cannot have an existing company ID")

    clean_name = (
        name.strip()
    )

    if not clean_name:
        raise HTTPException(
            status_code=400,
            detail=(
                "Organization name is required"
            ),
        )

    minimum_match_score = max(
        0,
        min(
            minimum_match_score,
            100,
        ),
    )

    with SessionLocal() as db:
        organization = None if create_new else db.scalar(
            select(
                Organization
            )
            .where(
                Organization
                .is_active
                .is_(True)
            )
            .where(Organization.id == organization_id if organization_id else true())
            .order_by(
                Organization
                .created_at
                .asc()
            )
            .limit(1)
        )

        if organization_id is not None and organization is None:
            raise HTTPException(status_code=404, detail="Company not found")

        if organization is None:
            organization = (
                Organization(
                    name=clean_name,
                )
            )

            db.add(
                organization
            )

            db.flush()

        organization.name = (
            clean_name
        )

        organization.country = (
            country.strip()
            or None
        )

        organization.city = (
            city.strip()
            or None
        )

        organization.website = (
            website.strip()
            or None
        )

        organization.industry = (
            industry.strip()
            or None
        )

        organization.description = (
            description.strip()
            or None
        )

        organization.default_language = (
            default_language.strip()
            or "en"
        )

        organization.timezone = (
            timezone_name.strip()
            or None
        )

        preference = db.scalar(
            select(
                OpportunityPreference
            )
            .where(
                OpportunityPreference
                .organization_id
                == organization.id
            )
        )

        if preference is None:
            preference = (
                OpportunityPreference(
                    organization_id=(
                        organization.id
                    ),

                    countries=[],
                    regions=[],
                    industries=[],
                    services=[],
                    opportunity_types=[],
                    keywords=[],
                    languages=[],
                )
            )

            db.add(
                preference
            )

        preference.countries = (
            parse_list_field(
                countries
            )
        )

        preference.regions = (
            parse_list_field(
                regions
            )
        )

        preference.industries = (
            parse_list_field(
                industries
            )
        )

        preference.services = (
            parse_list_field(
                services
            )
        )

        preference.opportunity_types = (
            parse_list_field(
                opportunity_types
            )
        )

        preference.keywords = (
            parse_list_field(
                keywords
            )
        )

        preference.languages = (
            parse_list_field(
                languages
            )
        )

        preference.minimum_match_score = (
            minimum_match_score
        )

        preference.include_no_deadline = (
            include_no_deadline
            is not None
        )

        preference.include_jobs = (
            include_jobs
            is not None
        )

        preference.include_tenders = (
            include_tenders
            is not None
        )

        preference.include_grants = (
            include_grants
            is not None
        )

        preference.include_partnerships = (
            include_partnerships
            is not None
        )

        submitted_intelligence = {
            "capabilities": capabilities,
            "areas_of_expertise": areas_of_expertise,
            "assignment_types": assignment_types,
            "client_types": client_types,
            "sector_experience": sector_experience,
            "relevant_experience": relevant_experience,
            "track_record": track_record,
            "preferred_sectors": preferred_sectors,
            "excluded_services": excluded_services,
            "excluded_sectors": excluded_sectors,
            "excluded_opportunity_types": excluded_opportunity_types,
            "excluded_countries": excluded_countries,
        }
        intelligence = dict(preference.intelligence or {})
        for field, value in submitted_intelligence.items():
            # Older form clients must not silently erase newly recorded evidence.
            if rich_profile or value is not None:
                intelligence[field] = (value or "").splitlines()
        preference.intelligence = CompanyIntelligence.model_validate(
            intelligence
        ).model_dump(mode="json")

        db.commit()
        saved_company_id = organization.id

    return RedirectResponse(
        url=f"/business-profile?saved=1&company_id={saved_company_id}",
        status_code=303,
    )


@app.get("/sources")
def sources_page(
    request: Request,
    q: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
):
    statement = select(Source)

    if q:
        search_term = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                Source.organisation_name.ilike(search_term),
                Source.domain.ilike(search_term),
                Source.source_type.ilike(search_term),
            )
        )

    if status == "active":
        statement = statement.where(Source.is_active.is_(True))
    elif status == "disabled":
        statement = statement.where(Source.is_active.is_(False))
    elif status == "auto_disabled":
        statement = statement.where(Source.is_auto_disabled.is_(True))

    statement = statement.order_by(
        Source.is_active.desc(),
        Source.priority_score.desc(),
        Source.confidence_score.desc(),
        Source.organisation_name.asc(),
    )

    with SessionLocal() as db:
        source_companies = list(db.scalars(select(Organization).where(Organization.is_active.is_(True)))) if principal.get() else []
        sources = list(db.scalars(statement).all())
        (
            source_count,
            active_count,
            auto_disabled_count,
            total_opportunities_found,
        ) = db.execute(
            select(
                func.count(Source.id),
                func.coalesce(
                    func.sum(case((Source.is_active.is_(True), 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case((Source.is_auto_disabled.is_(True), 1), else_=0)
                    ),
                    0,
                ),
                func.coalesce(func.sum(Source.total_opportunities_found), 0),
            )
        ).one()

    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context={
            "sources": sources,
            "companies": source_companies,
            "source_count": int(cast(int, source_count)),
            "filtered_count": len(sources),
            "active_count": int(cast(int, active_count)),
            "disabled_count": int(cast(int, source_count)) - int(cast(int, active_count)),
            "auto_disabled_count": int(cast(int, auto_disabled_count)),
            "total_opportunities_found": int(cast(int, total_opportunities_found)),
            "selected_q": q or "",
            "selected_status": status or "",
        },
    )


@app.post("/sources/{source_id}/disable")
def disable_source(source_id: int):
    with SessionLocal() as db:
        source = db.get(Source, source_id)

        if source is None:
            raise HTTPException(
                status_code=404,
                detail="Source not found",
            )

        source.is_active = False
        source.disabled_reason = "Manually disabled."
        db.commit()

    return RedirectResponse(
        url="/sources",
        status_code=303,
    )


@app.post("/sources/{source_id}/enable")
def enable_source(source_id: int):
    with SessionLocal() as db:
        source = db.get(Source, source_id)

        if source is None:
            raise HTTPException(
                status_code=404,
                detail="Source not found",
            )

        source.is_active = True
        source.is_approved = True
        source.is_auto_disabled = False
        source.consecutive_failures = 0
        source.disabled_reason = None
        db.commit()

    return RedirectResponse(
        url="/sources",
        status_code=303,
    )


@app.post("/scan")
def scan_internet():
    if principal.get() is not None:
        raise HTTPException(status_code=400, detail="Use Analyze an opportunity for a bounded scan of a selected notice URL. Full discovery is an operator job.")
    run_scanner()

    return RedirectResponse(
        url="/",
        status_code=303,
    )


@app.post("/ai-review")
def run_manual_ai_review():
    if principal.get() is not None:
        raise HTTPException(status_code=400, detail="Use the opportunity refresh action. Bulk AI review is an operator job.")
    summary = run_ai_review()

    redirect_url = (
        "/?"
        f"ai_reviewed={summary.reviewed}"
        f"&ai_accepted={summary.accepted}"
        f"&ai_rejected={summary.rejected}"
        f"&ai_needs_review={summary.needs_review}"
        f"&ai_unavailable={summary.unavailable}"
        f"&ai_remaining={summary.remaining_unreviewed}"
    )

    return RedirectResponse(
        url=redirect_url,
        status_code=303,
    )


from app.beta_routes import router as beta_router  # noqa: E402
app.include_router(beta_router)
