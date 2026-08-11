from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

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
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings
from app.database import Base, SessionLocal, engine
from app.filters import (
    BusinessMatchPreferences,
    score_for_business,
)
from app.models import (
    LEAD_PRIORITIES,
    LEAD_STATUSES,
    OPPORTUNITY_STATUSES,
    Opportunity,
    OpportunityPreference,
    Organization,
    Source,
)
from app.schemas import FilteredOpportunity
from app.internal_scheduler import scheduler
from app.scanner import run_scanner


BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_PAGE_SIZE = 50


@asynccontextmanager
async def lifespan(_app: FastAPI):
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


Base.metadata.create_all(
    bind=engine
)


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
        Opportunity.is_lead.is_(False),
        or_(
            Opportunity.is_expired.is_(True),

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
        Opportunity.is_lead.is_(True),

        and_(
            Opportunity.is_lead.is_(False),
            Opportunity.is_expired.is_(False),

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
        Opportunity.is_expired.is_(True),

        and_(
            Opportunity.deadline.is_not(
                None
            ),
            Opportunity.deadline < today,
        ),
    )

    active_condition = and_(
        opportunity_inbox_condition(now),
        Opportunity.is_expired.is_(False),

        or_(
            Opportunity.deadline.is_(None),
            Opportunity.deadline >= today,
        ),
    )

    expiring_soon_condition = and_(
        Opportunity.is_expired.is_(False),
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
                    Opportunity.is_lead.is_(
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
                Opportunity.status,
                func.count(
                    Opportunity.id
                ),
            )
            .where(
                inbox_condition
            )
            .group_by(
                Opportunity.status
            )
        ).all()

    for status, count in results:
        if status:
            counts[status] = count

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
            opportunity.display_match_score = (
                opportunity.match_score
                or 0
            )

            opportunity.display_match_reason = (
                opportunity.match_reason
                or ""
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

        opportunity.display_match_score = (
            result.score
        )

        opportunity.display_match_reason = (
            result.reason
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
    Sort personalized opportunities by relevance,
    then freshness, then deadline.
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

    return (
        -score,
        -discovered.timestamp(),
        deadline,
    )


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
            opportunity.first_discovered_at
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
            opportunity.first_discovered_at
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

    organization, preference = (
        get_active_business_profile()
    )

    business_preferences = (
        build_business_match_preferences(
            preference
        )
    )

    statement = select(
        Opportunity
    )

    if deadline_filter == "archived":
        statement = statement.where(
            archive_condition
        )

    elif deadline_filter == "expired":
        statement = statement.where(
            or_(
                Opportunity
                .is_expired
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
            Opportunity.status
            == status
        )

    if deadline_filter == "open":
        statement = statement.where(
            Opportunity.is_expired.is_(
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
        candidate_opportunities = list(
            db.scalars(
                statement
            ).all()
        )

        inbox_candidates = list(
            db.scalars(
                select(
                    Opportunity
                )
                .where(
                    inbox_condition
                )
            ).all()
        )

    personalized_candidates = (
        personalize_opportunities(
            candidate_opportunities,
            business_preferences,
        )
    )

    personalized_candidates.sort(
        key=opportunity_sort_key
    )

    personalized_inbox = (
        personalize_opportunities(
            inbox_candidates,
            business_preferences,
        )
    )

    filtered_total = len(
        personalized_candidates
    )

    total_opportunities = len(
        personalized_inbox
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
        personalized_candidates[
            start_index:end_index
        ]
    )

    dashboard_metrics = (
        get_dashboard_metrics(
            now
        )
    )

    dashboard_metrics = (
        personalize_dashboard_metrics(
            dashboard_metrics,
            personalized_inbox,
            now,
        )
    )

    status_counts = (
        get_status_counts(
            inbox_condition
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

            "status_counts": (
                status_counts
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

            "inbox_no_deadline_max_age_days": (
                get_settings()
                .inbox_no_deadline_max_age_days
            ),

            "business_profile_active": (
                business_preferences
                is not None
            ),

            "business_profile_name": (
                organization.name
                if organization
                else None
            ),

            "business_minimum_match_score": (
                business_preferences
                .minimum_match_score
                if business_preferences
                else 0
            ),
        },
    )


@app.get(
    "/opportunities/{opportunity_id}"
)
def opportunity_detail(
    opportunity_id: int,
    request: Request,
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

        if opportunity.status == "New":
            opportunity.status = (
                "Seen"
            )

            db.commit()

            db.refresh(
                opportunity
            )

    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "opportunity": (
                opportunity
            ),

            "statuses": (
                OPPORTUNITY_STATUSES
            ),

            "lead_statuses": (
                LEAD_STATUSES
            ),

            "lead_priorities": (
                LEAD_PRIORITIES
            ),

            "today": (
                date.today()
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

        opportunity.is_lead = (
            False
        )

        db.commit()

    return RedirectResponse(
        url="/pipeline",
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
                Opportunity
                .is_lead
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
):
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

        preference = None

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
):
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

        db.commit()

    return RedirectResponse(
        url="/business-profile?saved=1",
        status_code=303,
    )


@app.post("/scan")
def scan_internet():
    run_scanner()

    return RedirectResponse(
        url="/",
        status_code=303,
    )
