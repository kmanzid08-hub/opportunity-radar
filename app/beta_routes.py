"""Invite-only access and the minimal private beta user workflows."""

from datetime import timedelta
import logging
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from app import database
from app.beta_models import BetaSession, BetaUser, Workspace
from app.beta_scope import principal
from app.beta_security import (
    SESSION_COOKIE,
    SESSION_HOURS,
    aware,
    digest,
    error_page,
    hash_password,
    secure_cookies,
    take_limit,
    utcnow,
    verify_password,
)
from app.beta_submission import (
    fetch_source_candidates,
    process_raw_notice,
    refresh_match,
    submit_notice,
)
from app.models import AIOpportunityAnalysis, Organization, Source

router = APIRouter()
logger = logging.getLogger(__name__)


def page(request: Request, name: str, **context):
    from app.main import templates

    return templates.TemplateResponse(request=request, name=name, context=context)


@router.get("/healthz")
def health():
    return {"status": "ok"}


@router.get("/readyz")
def ready():
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        expected = ScriptDirectory.from_config(config).get_current_head()
        with database.SessionLocal() as db:
            actual = db.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        if actual != expected:
            return error_page("Database migration is required.", 503)
    except Exception as exc:
        logger.error(
            "Readiness check failed: exception_type=%s db_error_type=%s sqlstate=%s",
            type(exc).__name__,
            type(getattr(exc, "orig", None)).__name__,
            getattr(getattr(exc, "orig", None), "sqlstate", None),
        )
        try:
            with database.SessionLocal() as diagnostic_db:
                db_name, schema_name, version_table = diagnostic_db.execute(
                    text("SELECT current_database(), current_schema(), "
                         "to_regclass('alembic_version')::text")
                ).one()
                logger.error(
                    "Readiness database diagnostics: database=%s schema=%s alembic_table=%s",
                    db_name, schema_name, version_table is not None,
                )
        except Exception as diagnostic_exc:
            logger.error(
                "Readiness database diagnostics unavailable: exception_type=%s",
                type(diagnostic_exc).__name__,
            )
        return error_page("The service is temporarily unavailable.", 503)
    return {"status": "ready"}


@router.get("/login")
def login_page(request: Request):
    return page(request, "access.html", invite=False)


@router.get("/accept-invite")
def invite_page(request: Request):
    return page(request, "access.html", invite=True)


def signed_in(user_id: int) -> RedirectResponse:
    token = secrets.token_urlsafe(32)
    with database.SessionLocal() as db:
        db.execute(delete(BetaSession).where(BetaSession.expires_at < utcnow()))
        db.add(
            BetaSession(
                token_hash=digest(token),
                user_id=user_id,
                expires_at=utcnow() + timedelta(hours=SESSION_HOURS),
            )
        )
        db.commit()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=secure_cookies(),
        samesite="strict",
        path="/",
    )
    return response


@router.post("/login")
def login(
    request: Request,
    email: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=256)],
):
    if not take_limit(f"login-account:{digest(email.strip().casefold())}", 10, 900):
        return error_page("Too many sign-in attempts. Try again in 15 minutes.", 429)
    with database.SessionLocal() as db:
        user = db.scalar(
            select(BetaUser).where(BetaUser.email == email.strip().casefold())
        )
        valid = verify_password(password, user.password_hash if user else None)
        if not valid or user is None or not user.is_active:
            return page(
                request,
                "access.html",
                invite=False,
                error="Email or password was not recognized.",
            )
        uid = user.id
    return signed_in(uid)


@router.post("/accept-invite")
def accept_invite(
    request: Request,
    token: Annotated[str, Form(max_length=200)],
    password: Annotated[str, Form(min_length=12, max_length=256)],
):
    with database.SessionLocal() as db:
        user = db.scalar(
            select(BetaUser).where(BetaUser.invite_hash == digest(token.strip()))
        )
        if (
            user is None
            or not user.is_active
            or user.invite_expires_at is None
            or aware(user.invite_expires_at) <= utcnow()
        ):
            return page(
                request,
                "access.html",
                invite=True,
                error="This invitation is invalid or expired. Ask your beta administrator for a new invitation.",
            )
        uid = user.id
        # Consume atomically; two simultaneous submissions cannot reuse an invite.
        result = db.execute(
            update(BetaUser)
            .where(BetaUser.id == uid, BetaUser.invite_hash == digest(token.strip()))
            .values(
                password_hash=hash_password(password),
                invite_hash=None,
                invite_expires_at=None,
            )
            .returning(BetaUser.id)
        )
        if result.scalar_one_or_none() is None:
            db.rollback()
            return error_page("This invitation has already been used.", 400)
        db.commit()
    return signed_in(uid)


@router.post("/logout")
def logout(request: Request):
    with database.SessionLocal() as db:
        db.execute(
            delete(BetaSession).where(
                BetaSession.token_hash
                == digest(request.cookies.get(SESSION_COOKIE, ""))
            )
        )
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def team_page(request: Request, *, invitation_code: str | None = None,
              invitation_email: str | None = None, team_message: str | None = None,
              team_error: str | None = None):
    """Render current-workspace membership; never disclose password hashes."""
    identity = principal.get()
    assert identity is not None
    with database.SessionLocal() as db:
        workspace = db.get(Workspace, identity.workspace_id)
        members = (
            list(db.scalars(
                select(BetaUser)
                .where(BetaUser.workspace_id == identity.workspace_id)
                .order_by(BetaUser.email.asc())
            ))
            if identity.is_admin else []
        )
        # Keep ORM instances usable after the session closes.
        member_rows = [
            {
                "id": member.id,
                "email": member.email,
                "is_admin": member.is_admin,
                "is_active": member.is_active,
                "pending": member.password_hash is None,
                "invite_expires_at": member.invite_expires_at,
            }
            for member in members
        ]
        workspace_name = workspace.name if workspace is not None else ""
    return page(
        request, "settings.html", workspace_name=workspace_name,
        team_members=member_rows, invitation_code=invitation_code,
        invitation_email=invitation_email, team_message=team_message,
        team_error=team_error,
    )


@router.get("/settings")
def settings_page(request: Request):
    return team_page(request)


def require_team_admin():
    identity = principal.get()
    if identity is None or not identity.is_admin:
        raise HTTPException(403, "Only a workspace administrator can manage users.")
    return identity


def valid_account_email(email: str) -> str | None:
    email = email.strip().casefold()
    # Keep usernames predictable; avoid whitespace and exotic Unicode email
    # forms that a browser could normalize inconsistently at sign-in.
    if not 3 <= len(email) <= 254 or not email.isascii():
        return None
    if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", email):
        return None
    if ".." in email or email.startswith(".") or "@." in email:
        return None
    return email


@router.post("/settings/team/invite")
def invite_teammate(
    request: Request,
    email: Annotated[str, Form(max_length=254)],
):
    """Admin creates a scoped login; invitee chooses their own password."""
    identity = require_team_admin()
    if not take_limit(f"team-invite:user:{identity.user_id}", 10, 3600):
        return error_page("Too many invitations. Try again later.", 429)
    normalized = valid_account_email(email)
    if normalized is None:
        return team_page(request, team_error="Enter a valid email address.")
    token = secrets.token_urlsafe(32)
    with database.SessionLocal() as db:
        if db.scalar(select(BetaUser.id).where(BetaUser.email == normalized)):
            return team_page(
                request,
                team_error="That email already has an account. Use a replacement invitation if necessary.",
            )
        db.add(BetaUser(
            workspace_id=identity.workspace_id,
            email=normalized,
            password_hash=None,
            is_active=True,
            is_admin=False,
            invite_hash=digest(token),
            invite_expires_at=utcnow() + timedelta(hours=48),
        ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return team_page(
                request, team_error="An account with that email already exists."
            )
    # Plaintext token exists only in this response; the DB stores its hash.
    return team_page(
        request, invitation_code=token, invitation_email=normalized,
        team_message="Account created. Share this one-use invitation code privately.",
    )


@router.post("/settings/team/reinvite")
def reinvite_teammate(
    request: Request,
    user_id: Annotated[int, Form(ge=1)],
):
    identity = require_team_admin()
    if not take_limit(f"team-invite:user:{identity.user_id}", 10, 3600):
        return error_page("Too many invitations. Try again later.", 429)
    token = secrets.token_urlsafe(32)
    with database.SessionLocal() as db:
        member = db.scalar(select(BetaUser).where(
            BetaUser.id == user_id, BetaUser.workspace_id == identity.workspace_id
        ))
        if member is None:
            raise HTTPException(404, "Team member not found.")
        if member.is_admin or member.id == identity.user_id:
            raise HTTPException(403, "Cannot reset an administrator using this form.")
        email = member.email
        member.is_active = True
        member.password_hash = None
        member.invite_hash = digest(token)
        member.invite_expires_at = utcnow() + timedelta(hours=48)
        db.execute(delete(BetaSession).where(BetaSession.user_id == member.id))
        db.commit()
    return team_page(
        request, invitation_code=token, invitation_email=email,
        team_message="Previous credentials revoked. Share the new one-use code privately.",
    )


@router.post("/settings/team/deactivate")
def deactivate_teammate(
    request: Request,
    user_id: Annotated[int, Form(ge=1)],
):
    identity = require_team_admin()
    with database.SessionLocal() as db:
        member = db.scalar(select(BetaUser).where(
            BetaUser.id == user_id, BetaUser.workspace_id == identity.workspace_id
        ))
        if member is None:
            raise HTTPException(404, "Team member not found.")
        if member.is_admin or member.id == identity.user_id:
            raise HTTPException(403, "Administrator accounts cannot be deactivated here.")
        member.is_active = False
        member.invite_hash = None
        member.invite_expires_at = None
        db.execute(delete(BetaSession).where(BetaSession.user_id == member.id))
        db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/password")
def change_password(
    request: Request,
    current_password: Annotated[str, Form(max_length=256)],
    password: Annotated[str, Form(min_length=12, max_length=256)],
):
    identity = principal.get()
    assert identity is not None
    with database.SessionLocal() as db:
        user = db.get(BetaUser, identity.user_id)
        if user is None or not verify_password(current_password, user.password_hash):
            return error_page("Your current password was not recognized.", 400)
        user.password_hash = hash_password(password)
        db.execute(delete(BetaSession).where(BetaSession.user_id == user.id))
        db.commit()
    return signed_in(identity.user_id)


@router.get("/analyze")
def analyze_page(request: Request):
    with database.SessionLocal() as db:
        companies = list(
            db.scalars(select(Organization).where(Organization.is_active.is_(True)))
        )
    return page(request, "analyze.html", companies=companies)


@router.post("/analyze")
def analyze(
    request: Request,
    company_id: Annotated[int, Form(ge=1)],
    url: Annotated[str, Form(max_length=2048)] = "",
    notice_text: Annotated[str, Form(max_length=50_000)] = "",
):
    from app.intelligence.provenance import public_url

    url, notice_text = url.strip(), notice_text.strip()
    with database.SessionLocal() as db:
        if db.get(Organization, company_id) is None:
            raise HTTPException(404, "Company not found")
    if (not url and not notice_text) or (
        url and (public_url(url) is None or "#" in url)
    ):
        return error_page(
            "Paste a public http(s) opportunity URL or the opportunity notice text. Remove URL fragments and credentials.",
            400,
        )
    url = public_url(url) or ""
    try:
        oid, result = submit_notice(company_id, url, notice_text)
    except Exception:
        identity = principal.get()
        logger.warning(
            "Beta submission failed workspace=%s",
            identity.workspace_id if identity else None,
        )
        return error_page(
            "The notice could not be fetched or analyzed. Paste the notice text using Analyze an opportunity, or retry later. Website access restrictions are respected.",
            502,
        )
    if oid is not None:
        return RedirectResponse(
            f"/opportunities/{oid}?company_id={company_id}", status_code=303
        )
    return RedirectResponse(f"/assessments?result={result}", status_code=303)


@router.get("/assessments")
def assessments(request: Request):
    user = principal.get()
    assert user is not None
    with database.SessionLocal() as db:
        records = list(
            db.scalars(
                select(AIOpportunityAnalysis)
                .where(AIOpportunityAnalysis.workspace_id == user.workspace_id)
                .order_by(AIOpportunityAnalysis.analyzed_at.desc())
                .limit(100)
            )
        )
    return page(request, "assessments.html", records=records)


@router.post("/opportunities/{opportunity_id}/refresh")
def refresh(opportunity_id: int, company_id: Annotated[int, Form(ge=1)]):
    from app.models import Opportunity

    with database.SessionLocal() as db:
        if (
            db.get(Organization, company_id) is None
            or db.get(Opportunity, opportunity_id) is None
        ):
            raise HTTPException(404, "Opportunity or company not found")
    try:
        refresh_match(opportunity_id, company_id)
    except Exception:
        return error_page("The assessment could not be refreshed. Retry later.", 502)
    return RedirectResponse(
        f"/opportunities/{opportunity_id}?company_id={company_id}", status_code=303
    )


@router.post("/scan-source")
def scan_selected_source(
    company_id: Annotated[int, Form(ge=1)], source_id: Annotated[int, Form(ge=1)]
):
    with database.SessionLocal() as db:
        company = db.get(Organization, company_id)
        source = db.get(Source, source_id)
        if company is None or not company.is_active:
            raise HTTPException(404, "Company not found")
        if source is None or not source.is_active or not source.is_approved:
            raise HTTPException(404, "Active approved source not found")
    try:
        candidates = fetch_source_candidates(source)
        if not candidates:
            return error_page(
                "No readable notices were found in this bounded scan. Analyze a specific tender URL or paste its notice text.",
                502,
            )
        for raw in candidates[:3]:
            process_raw_notice(
                company_id, raw, raw.source_url, raw.description or raw.title
            )
    except Exception:
        return error_page(
            "The source scan could not finish. Saved assessments are available under Submitted assessments. Retry or paste the notice text.",
            502,
        )
    return RedirectResponse("/assessments?scanned=1", status_code=303)


@router.post("/sources/add-rwanda")
def add_rwanda_sources():
    """Register public Rwanda procurement portals without fetching or analyzing them."""
    from sqlalchemy.exc import IntegrityError

    identity = principal.get()
    if identity is None or not identity.is_admin:
        raise HTTPException(403, "This action requires a beta administrator")

    sources = (
        ("Job in Rwanda – Tenders", "https://www.jobinrwanda.com/jobs/tender", "jobinrwanda.com"),
        ("Job in Rwanda – Consultancies", "https://www.jobinrwanda.com/jobs/consultancy", "jobinrwanda.com"),
        ("The New Times – Jobs & Tenders", "https://jobs.newtimes.co.rw/", "jobs.newtimes.co.rw"),
        ("Rwanda ICT Chamber – Opportunities", "https://ictchamber.rw/opportunities", "ictchamber.rw"),
    )
    with database.SessionLocal() as db:
        for name, monitor_url, domain in sources:
            if db.scalar(select(Source.id).where(Source.monitor_url == monitor_url)):
                continue
            with db.begin_nested():
                db.add(Source(
                    organisation_name=name,
                    monitor_url=monitor_url,
                    base_url="https://" + domain + "/",
                    domain=domain,
                    source_type="Rwanda tenders and consultancy",
                    source_role="DISCOVERY_ONLY",
                    is_active=True,
                    is_approved=True,
                ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return error_page("Some sources were added concurrently. Reload and retry.", 409)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/add")
def add_source(
    name: Annotated[str, Form(min_length=1, max_length=255)],
    url: Annotated[str, Form(max_length=2048)],
):
    from urllib.parse import urlsplit
    from sqlalchemy.exc import IntegrityError
    from app.intelligence.provenance import public_url

    identity = principal.get()
    if identity is None or not identity.is_admin:
        raise HTTPException(403, "This action requires a beta administrator")
    normalized = public_url(url.strip())
    if normalized is None or "#" in url:
        return error_page(
            "Enter a public http(s) source URL without credentials or fragments.", 400
        )
    parts = urlsplit(normalized)
    with database.SessionLocal() as db:
        if db.scalar(select(Source.id).where(Source.monitor_url == normalized)) is None:
            db.add(
                Source(
                    organisation_name=name.strip(),
                    monitor_url=normalized,
                    base_url=f"{parts.scheme}://{parts.netloc}/",
                    domain=parts.hostname or "",
                    is_active=True,
                    is_approved=True,
                    source_role="UNKNOWN",
                )
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()  # Concurrent duplicate addition is harmless.
    return RedirectResponse("/sources", status_code=303)
