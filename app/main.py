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
from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import (
    LEAD_PRIORITIES,
    LEAD_STATUSES,
    OPPORTUNITY_STATUSES,
    Opportunity,
)
from app.internal_scheduler import scheduler
from app.scanner import run_scanner


BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Keep the internal scheduler for local use, but disable it on Render.
    # GitHub Actions runs the production schedules reliably while the free
    # Render web service may be asleep.
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
        "Professional services opportunity monitoring platform"
    ),
    lifespan=lifespan,
)

Base.metadata.create_all(bind=engine)

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

templates = Jinja2Templates(
    directory=BASE_DIR / "templates"
)


def current_utc_time() -> datetime:
    return datetime.now(timezone.utc)


def opportunity_archive_condition(
    now: datetime,
) -> ColumnElement[bool]:
    """Return the rule for records hidden from the opportunity inbox."""
    settings = get_settings()
    today = now.date()
    stale_before = now - timedelta(
        days=settings.inbox_no_deadline_max_age_days
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
                Opportunity.deadline.is_not(None),
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
    """Return records that remain actionable in the opportunity inbox."""
    settings = get_settings()
    today = now.date()
    stale_before = now - timedelta(
        days=settings.inbox_no_deadline_max_age_days
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


def get_status_counts(
    inbox_condition: ColumnElement[bool],
) -> dict[str, int]:
    """
    Return opportunity counts for each workflow status.
    """
    counts = {
        status: 0
        for status in OPPORTUNITY_STATUSES
    }

    with SessionLocal() as db:
        results = db.execute(
            select(
                Opportunity.status,
                func.count(Opportunity.id),
            )
            .where(inbox_condition)
            .group_by(Opportunity.status)
        ).all()

    for status, count in results:
        if status:
            counts[status] = count

    return counts


def get_categories(
    inbox_condition: ColumnElement[bool],
) -> list[str]:
    """
    Return all opportunity categories currently in the database.
    """
    with SessionLocal() as db:
        categories = db.scalars(
            select(Opportunity.category)
            .where(inbox_condition)
            .distinct()
            .order_by(Opportunity.category)
        ).all()

    return [
        category
        for category in categories
        if category
    ]


@app.get("/")
def home(
    request: Request,
    q: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    deadline_filter: Annotated[
        str | None,
        Query(),
    ] = None,
):
    """
    Display the dashboard with search and filtering.
    """
    now = current_utc_time()
    today = now.date()
    inbox_condition = opportunity_inbox_condition(
        now
    )
    archive_condition = opportunity_archive_condition(
        now
    )

    statement = select(Opportunity)

    if deadline_filter == "archived":
        statement = statement.where(
            archive_condition
        )
    elif deadline_filter == "expired":
        statement = statement.where(
            or_(
                Opportunity.is_expired.is_(True),
                and_(
                    Opportunity.deadline.is_not(None),
                    Opportunity.deadline < today,
                ),
            )
        )
    else:
        statement = statement.where(
            inbox_condition
        )

    if q:
        search_term = f"%{q.strip()}%"

        statement = statement.where(
            or_(
                Opportunity.title.ilike(
                    search_term
                ),
                Opportunity.organisation_name.ilike(
                    search_term
                ),
                Opportunity.description.ilike(
                    search_term
                ),
                Opportunity.category.ilike(
                    search_term
                ),
            )
        )

    if category:
        statement = statement.where(
            Opportunity.category == category
        )

    if status:
        statement = statement.where(
            Opportunity.status == status
        )

    if deadline_filter == "open":
        statement = statement.where(
            Opportunity.is_expired.is_(False),
            or_(
                Opportunity.deadline >= today,
                Opportunity.deadline.is_(None),
            )
        )

    elif deadline_filter == "no_deadline":
        statement = statement.where(
            Opportunity.deadline.is_(None)
        )

    elif deadline_filter == "due_7_days":
        seven_days_from_now = (
            today + timedelta(days=7)
        )

        statement = statement.where(
            Opportunity.deadline.is_not(None),
            Opportunity.deadline >= today,
            Opportunity.deadline
            <= seven_days_from_now,
        )

    elif deadline_filter == "due_30_days":
        thirty_days_from_now = (
            today + timedelta(days=30)
        )

        statement = statement.where(
            Opportunity.deadline.is_not(None),
            Opportunity.deadline >= today,
            Opportunity.deadline
            <= thirty_days_from_now,
        )

    statement = statement.order_by(
        Opportunity.is_expired.asc(),
        Opportunity.deadline.is_(None),
        Opportunity.deadline.asc(),
        Opportunity.first_discovered_at.desc(),
    )

    with SessionLocal() as db:
        opportunities = db.scalars(
            statement
        ).all()

        total_opportunities = db.scalar(
            select(
                func.count(Opportunity.id)
            ).where(inbox_condition)
        ) or 0

    status_counts = get_status_counts(
        inbox_condition
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "opportunities": opportunities,
            "filtered_count": len(
                opportunities
            ),
            "total_opportunities": (
                total_opportunities
            ),
            "categories": get_categories(
                inbox_condition
            ),
            "statuses": (
                OPPORTUNITY_STATUSES
            ),
            "status_counts": status_counts,
            "today": today,
            "selected_q": q or "",
            "selected_category": (
                category or ""
            ),
            "selected_status": status or "",
            "selected_deadline": (
                deadline_filter or ""
            ),
            "inbox_no_deadline_max_age_days": (
                get_settings()
                .inbox_no_deadline_max_age_days
            ),
        },
    )


@app.get("/opportunities/{opportunity_id}")
def opportunity_detail(
    opportunity_id: int,
    request: Request,
):
    """
    Display the full details of one opportunity.
    """
    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity not found",
            )

        if opportunity.status == "New":
            opportunity.status = "Seen"
            db.commit()
            db.refresh(opportunity)

    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
             "opportunity": opportunity,
             "statuses": OPPORTUNITY_STATUSES,
             "lead_statuses": LEAD_STATUSES,
             "lead_priorities": LEAD_PRIORITIES,
             "today": date.today(),
},
    )


@app.post(
    "/opportunities/{opportunity_id}/update"
)
def update_opportunity(
    opportunity_id: int,
    status: Annotated[str, Form()],
    user_notes: Annotated[str, Form()] = "",
):
    """
    Update workflow status and internal notes.
    """
    if status not in OPPORTUNITY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Invalid opportunity status",
        )

    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity not found",
            )

        opportunity.status = status
        opportunity.user_notes = (
            user_notes.strip() or None
        )

        if status == "Expired":
            opportunity.is_expired = True
        elif (
            opportunity.deadline is None
            or opportunity.deadline
            >= date.today()
        ):
            opportunity.is_expired = False

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
    status: Annotated[str, Form()],
):
    """
    Allow status changes directly from the dashboard.
    """
    if status not in OPPORTUNITY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Invalid opportunity status",
        )

    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity not found",
            )

        opportunity.status = status

        if status == "Expired":
            opportunity.is_expired = True
        elif (
            opportunity.deadline is None
            or opportunity.deadline
            >= date.today()
        ):
            opportunity.is_expired = False

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
    """
    Add an opportunity to the business development pipeline.
    """
    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity not found",
            )

        opportunity.is_lead = True
        opportunity.status = "Interested"

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
    """
    Remove an opportunity from the pipeline.
    """
    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity not found",
            )

        opportunity.is_lead = False

        db.commit()

    return RedirectResponse(
        url=(
            f"/opportunities/"
            f"{opportunity_id}"
        ),
        status_code=303,
    )

@app.get("/pipeline")
def pipeline_page(
    request: Request,
):
    """
    Display all opportunities currently in the pipeline.
    """
    with SessionLocal() as db:
        opportunities = db.scalars(
            select(Opportunity)
            .where(
                Opportunity.is_lead.is_(True)
            )
            .order_by(
                Opportunity.deadline.is_(None),
                Opportunity.deadline.asc(),
                Opportunity.first_discovered_at.desc(),
            )
        ).all()

    return templates.TemplateResponse(
        request=request,
        name="pipeline.html",
        context={
            "opportunities": opportunities,
            "pipeline_count": len(opportunities),
            "today": date.today(),
        },
    )

@app.post(
    "/opportunities/{opportunity_id}/pipeline/update"
)
def update_pipeline_opportunity(
    opportunity_id: int,
    assigned_to: Annotated[str, Form()] = "",
    lead_priority: Annotated[str, Form()] = "",
    lead_status: Annotated[str, Form()] = "",
    next_action: Annotated[str, Form()] = "",
    next_action_due: Annotated[str, Form()] = "",
    last_follow_up_date: Annotated[str, Form()] = "",
    lead_notes: Annotated[str, Form()] = "",
):
    """
    Update business development pipeline fields.
    """
    if (
        lead_priority
        and lead_priority not in LEAD_PRIORITIES
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid lead priority",
        )

    if (
        lead_status
        and lead_status not in LEAD_STATUSES
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid lead status",
        )

    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity not found",
            )

        if not opportunity.is_lead:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Opportunity is not in the pipeline"
                ),
            )

        opportunity.assigned_to = (
            assigned_to.strip() or None
        )

        opportunity.lead_priority = (
            lead_priority or None
        )

        opportunity.lead_status = (
            lead_status or None
        )

        opportunity.next_action = (
            next_action.strip() or None
        )

        opportunity.next_action_due = (
            date.fromisoformat(next_action_due)
            if next_action_due
            else None
        )

        opportunity.last_follow_up_date = (
            date.fromisoformat(
                last_follow_up_date
            )
            if last_follow_up_date
            else None
        )

        opportunity.lead_notes = (
            lead_notes.strip() or None
        )

        if lead_status == "Submitted":
            opportunity.status = "Applied"

        elif lead_status in {
            "Awarded",
            "Pursuing",
            "Under Review",
            "On Hold",
        }:
            opportunity.status = "Interested"

        elif lead_status in {
            "Lost",
            "Cancelled",
        }:
            opportunity.status = "Rejected"

        db.commit()

    return RedirectResponse(
        url=(
            f"/opportunities/"
            f"{opportunity_id}"
        ),
        status_code=303,
    )

@app.post("/scan")
def scan_internet():
    """
    Run all enabled opportunity scanners.
    """
    run_scanner()

    return RedirectResponse(
        url="/",
        status_code=303,
    )
