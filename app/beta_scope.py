"""Request-scoped ORM boundaries. Background operator jobs have no user scope."""

from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import event, or_, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria


@dataclass(frozen=True)
class Principal:
    user_id: int
    workspace_id: int
    email: str
    is_admin: bool


principal: ContextVar[Principal | None] = ContextVar("beta_principal", default=None)
private_submission: ContextVar[bool] = ContextVar("private_submission", default=False)
ai_attempts: ContextVar[int] = ContextVar("beta_ai_attempts", default=0)


@event.listens_for(Session, "do_orm_execute")
def scope_queries(state: ORMExecuteState) -> None:
    user = principal.get()
    if user is None:
        from app.models import Opportunity, AIOpportunityAnalysis

        # Background shared discovery must never deduplicate against private notices.
        state.statement = state.statement.options(
            with_loader_criteria(
                Opportunity, Opportunity.workspace_id.is_(None), include_aliases=True
            ),
            with_loader_criteria(
                AIOpportunityAnalysis,
                AIOpportunityAnalysis.workspace_id.is_(None),
                include_aliases=True,
            ),
        )
        return
    from app.models import (
        AIOpportunityAnalysis,
        CompanyOpportunityMatch,
        CompanyOpportunityReadiness,
        CompanyOpportunityTriage,
        Opportunity,
        OpportunityPreference,
        OpportunityRequirement,
        OpportunityRequirementExtraction,
        Organization,
    )
    from app.beta_models import BetaSession, BetaUser, Workspace, WorkspaceOpportunity
    from app.lead_models import Lead
    from app.proposal_models import Proposal

    workspace_id = user.workspace_id
    owned_companies = select(Organization.id).where(
        Organization.workspace_id == workspace_id
    )
    accessible_opportunities = select(Opportunity.id).where(
        or_(
            Opportunity.workspace_id.is_(None), Opportunity.workspace_id == workspace_id
        )
    )
    options = [
        with_loader_criteria(
            BetaUser, BetaUser.id == user.user_id, include_aliases=True
        ),
        with_loader_criteria(
            BetaSession, BetaSession.user_id == user.user_id, include_aliases=True
        ),
        with_loader_criteria(
            Workspace, Workspace.id == workspace_id, include_aliases=True
        ),
        with_loader_criteria(
            Organization,
            Organization.workspace_id == workspace_id,
            include_aliases=True,
        ),
        with_loader_criteria(
            WorkspaceOpportunity,
            WorkspaceOpportunity.workspace_id == workspace_id,
            include_aliases=True,
        ),
    ]
    for model in (
        OpportunityPreference,
        CompanyOpportunityMatch,
        CompanyOpportunityTriage,
    ):
        options.append(
            with_loader_criteria(
                model, model.organization_id.in_(owned_companies), include_aliases=True
            )
        )
    for company_model in (CompanyOpportunityReadiness, OpportunityRequirement):
        options.append(
            with_loader_criteria(
                company_model,
                company_model.company_id.in_(owned_companies),
                include_aliases=True,
            )
        )
    for notice_model in (Opportunity, AIOpportunityAnalysis):
        condition = (
            notice_model.workspace_id == workspace_id
            if private_submission.get()
            else or_(
                notice_model.workspace_id.is_(None),
                notice_model.workspace_id == workspace_id,
            )
        )
        options.append(
            with_loader_criteria(notice_model, condition, include_aliases=True)
        )
    for legacy_model in (Lead, Proposal):
        # Legacy global pursuit tables have no owner: never expose them to beta users.
        options.append(
            with_loader_criteria(
                legacy_model, legacy_model.id < 0, include_aliases=True
            )
        )
    options.append(
        with_loader_criteria(
            OpportunityRequirementExtraction,
            OpportunityRequirementExtraction.opportunity_id.in_(
                accessible_opportunities
            ),
            include_aliases=True,
        )
    )
    if (
        (state.is_update or state.is_delete)
        and state.bind_mapper is not None
        and state.bind_mapper.class_.__name__ not in {"BetaUsage", "BetaSession"}
    ):
        raise PermissionError("Bulk mutation is not permitted in a user request")
    state.statement = state.statement.options(*options)


@event.listens_for(Session, "before_flush")
def scope_writes(db: Session, *_args: object) -> None:
    user = principal.get()
    if user is None:
        return
    from app.models import Organization, Opportunity, AIOpportunityAnalysis
    from app.beta_models import WorkspaceOpportunity

    for item in db.new:
        if isinstance(
            item,
            (Organization, Opportunity, AIOpportunityAnalysis, WorkspaceOpportunity),
        ):
            if item.workspace_id is None:
                item.workspace_id = user.workspace_id
            if item.workspace_id != user.workspace_id:
                raise PermissionError("Workspace ownership mismatch")
    for item in db.dirty | db.deleted:
        if (
            isinstance(
                item,
                (
                    Organization,
                    Opportunity,
                    AIOpportunityAnalysis,
                    WorkspaceOpportunity,
                ),
            )
            and item.workspace_id != user.workspace_id
        ):
            raise PermissionError("Cannot change another workspace or shared record")


PRIVATE_FIELDS = (
    "status",
    "is_lead",
    "user_notes",
    "assigned_to",
    "lead_priority",
    "lead_status",
    "next_action",
    "next_action_due",
    "last_follow_up_date",
    "lead_notes",
)


def pursuit_column(name: str):
    """Use private pursuit state in SQL filters as well as displayed records."""
    from sqlalchemy import func
    from app.models import Opportunity
    from app.beta_models import WorkspaceOpportunity

    user = principal.get()
    if user is None:
        return getattr(Opportunity, name)
    default = False if name == "is_lead" else "New" if name == "status" else None
    value = (
        select(getattr(WorkspaceOpportunity, name))
        .where(
            WorkspaceOpportunity.workspace_id == user.workspace_id,
            WorkspaceOpportunity.opportunity_id == Opportunity.id,
        )
        .correlate(Opportunity)
        .scalar_subquery()
    )
    return func.coalesce(value, default) if default is not None else value


def private_state(db: Session, opportunity):
    """All user pursuit writes target the workspace record, never shared notices."""
    from app.beta_models import WorkspaceOpportunity

    user = principal.get()
    if user is None:
        return opportunity
    state = db.scalar(
        select(WorkspaceOpportunity).where(
            WorkspaceOpportunity.opportunity_id == opportunity.id,
            WorkspaceOpportunity.workspace_id == user.workspace_id,
        )
    )
    if state is None:
        state = WorkspaceOpportunity(
            workspace_id=user.workspace_id,
            opportunity_id=opportunity.id,
            status="New",
            is_lead=False,
        )
        db.add(state)
    # Used only for status validation in legacy route handlers; not persisted here.
    setattr(state, "deadline", opportunity.deadline)
    return state


@event.listens_for(Session, "loaded_as_persistent")
def overlay_private_state(db: Session, item: object) -> None:
    from app.models import Opportunity
    from app.beta_models import WorkspaceOpportunity
    from sqlalchemy.orm.attributes import set_committed_value

    user = principal.get()
    if user is None or not isinstance(item, Opportunity):
        return
    states = db.info.get("beta_pursuit_states")
    if states is None:
        states = {
            row.opportunity_id: row
            for row in db.scalars(
                select(WorkspaceOpportunity).where(
                    WorkspaceOpportunity.workspace_id == user.workspace_id,
                )
            )
        }
        db.info["beta_pursuit_states"] = states
    state = states.get(item.id)
    for name in PRIVATE_FIELDS:
        value = (
            getattr(state, name)
            if state
            else (False if name == "is_lead" else "New" if name == "status" else None)
        )
        set_committed_value(item, name, value)
    set_committed_value(item, "is_expired", item.is_expired or item.status == "Expired")


def pursuit_expired():
    from app.models import Opportunity

    if principal.get() is None:
        return Opportunity.is_expired
    return or_(Opportunity.is_expired.is_(True), pursuit_column("status") == "Expired")
