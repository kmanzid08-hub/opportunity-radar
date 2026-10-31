"""Opaque revocable sessions, CSRF, durable limits and production safeguards."""

from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import logging
import hmac
import os
import secrets

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app import database
from app.beta_models import BetaSession, BetaUsage, BetaUser
from app.beta_scope import Principal, ai_attempts, principal

SESSION_COOKIE = "radar_session"
CSRF_COOKIE = "radar_csrf"
SESSION_HOURS = 12


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Use a password between 12 and 256 characters.")
    salt = secrets.token_hex(16)
    result = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
    ).hex()
    return f"scrypt${salt}${result}"


def verify_password(password: str, stored: str | None) -> bool:
    # A dummy hash keeps unknown users on the same expensive verification path.
    stored = stored or f"scrypt${'0' * 32}${'0' * 128}"
    try:
        algorithm, salt, expected = stored.split("$")
        if algorithm != "scrypt" or len(password) > 256:
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
        return hmac.compare_digest(actual, expected)
    except ValueError:
        return False


def secure_cookies() -> bool:
    return os.environ.get("APP_ENV", "development").lower() == "production" or bool(
        os.environ.get("RENDER")
    )


def validate_production() -> None:
    if not secure_cookies():
        return
    from app.core.config import get_settings
    from urllib.parse import urlsplit

    origin = urlsplit(os.environ.get("PUBLIC_ORIGIN", ""))
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path
        or origin.query
        or origin.fragment
    ):
        raise RuntimeError(
            "Production requires PUBLIC_ORIGIN=https://your-host with no path"
        )
    if get_settings().enable_internal_scheduler:
        raise RuntimeError(
            "Disable the internal scheduler for the private beta web service"
        )
    if not get_settings().database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Production requires a PostgreSQL DATABASE_URL")
    from sqlalchemy.engine import make_url

    if make_url(get_settings().database_url).query.get("sslmode") not in {
        "require",
        "verify-ca",
        "verify-full",
    }:
        raise RuntimeError(
            "Production requires PostgreSQL TLS (sslmode=require or stronger)"
        )
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise RuntimeError("Production requires GEMINI_API_KEY")


def take_limit(key: str, limit: int, seconds: int = 3600) -> bool:
    """Atomic across workers; failed attempts consume quota too."""
    now = utcnow()
    bucket = int(now.timestamp()) // seconds
    bucket_key = f"{key}:{bucket}"
    with database.SessionLocal() as db:
        # Bounded lifecycle: discard expired counters, without touching user data.
        db.execute(delete(BetaUsage).where(BetaUsage.expires_at < now))
        db.commit()
        try:
            db.add(
                BetaUsage(
                    key=bucket_key,
                    count=0,
                    expires_at=now + timedelta(seconds=seconds * 2),
                )
            )
            db.commit()
        except IntegrityError:
            db.rollback()
        count = db.execute(
            update(BetaUsage)
            .where(BetaUsage.key == bucket_key, BetaUsage.count < limit)
            .values(count=BetaUsage.count + 1)
            .returning(BetaUsage.key)
        )
        reserved = count.scalar_one_or_none() is not None
        db.commit()
        return reserved


def reserve_ai_attempt() -> None:
    """Called by the existing centralized client before every provider attempt."""
    user = principal.get()
    if user is None:
        return
    from app.intelligence.gemini_client import GeminiRequestBudgetError

    attempts = ai_attempts.get()
    if attempts >= 9 or not take_limit(f"ai:workspace:{user.workspace_id}", 90, 86400):
        raise GeminiRequestBudgetError(
            "Your workspace analysis allowance has been reached. Try again later."
        )
    ai_attempts.set(attempts + 1)


def authenticate(request: Request) -> Principal | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    with database.SessionLocal() as db:
        row = db.execute(
            select(BetaSession, BetaUser)
            .join(BetaUser, BetaSession.user_id == BetaUser.id)
            .where(BetaSession.token_hash == digest(token))
        ).first()
        if row is None:
            return None
        session, user = row
        if not user.is_active or aware(session.expires_at) <= utcnow():
            return None
        return Principal(user.id, user.workspace_id, user.email, user.is_admin)


def error_page(message: str, status: int) -> HTMLResponse:
    # Messages here are constant strings; do not pass exception or user content.
    message = escape(message)
    return HTMLResponse(
        f'<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Opportunity Radar</title><main><h1>Opportunity Radar</h1><p role="alert">{message}</p><a href="/">Discover</a> · <a href="/analyze">Analyze an opportunity</a> · <a href="/login">Sign in</a></main></html>',
        status_code=status,
    )


class BetaSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        public = path in {
            "/login",
            "/accept-invite",
            "/healthz",
            "/readyz",
        } or path.startswith("/static/")
        try:
            user = None if public else authenticate(request)
        except Exception:
            logging.getLogger(__name__).error("Beta session storage unavailable")
            return error_page(
                "The service is temporarily unavailable. Please retry later.", 503
            )
        request.state.user = user
        session_token = request.cookies.get(SESSION_COOKIE, "")
        csrf = (
            digest("csrf:" + session_token)
            if session_token
            else request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        )
        request.state.csrf = csrf
        scope_token = principal.set(user)
        budget_token = ai_attempts.set(0)
        try:
            if not public and user is None:
                response: Response = RedirectResponse("/login", status_code=303)
            elif request.method not in {"GET", "HEAD", "OPTIONS"}:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 150_000:
                        return error_page(
                            "The submission is too large. Use at most 50,000 characters of notice text.",
                            413,
                        )
                request._body = bytes(body)
                try:
                    form = await request.form()
                except Exception:
                    return error_page(
                        "The form could not be read. Reload the page and try again.",
                        400,
                    )
                supplied = str(form.get("csrf_token", ""))
                origin = request.headers.get("origin")
                expected_origin = os.environ.get("PUBLIC_ORIGIN") or str(
                    request.base_url
                ).rstrip("/")
                token_matches = hmac.compare_digest(csrf, supplied)
                allowed_origins = {
                    expected_origin.rstrip("/"),
                    "https://opportunity-radar-gx12.onrender.com",
                }
                origin_matches = not origin or origin.rstrip("/") in allowed_origins
                if not token_matches or not origin_matches:
                    logging.getLogger(__name__).warning(
                        "CSRF submission rejected: path=%s token_matches=%s "
                        "origin_matches=%s cookie_present=%s form_token_present=%s",
                        path, token_matches, origin_matches,
                        bool(request.cookies.get(CSRF_COOKIE)), bool(supplied),
                    )
                    response = error_page(
                        "This form has expired. Reload the page and try again.", 403
                    )
                elif path in {"/login", "/accept-invite"} and not take_limit(
                    f"login:{digest(request.client.host if request.client else 'unknown')}",
                    20,
                    900,
                ):
                    response = error_page(
                        "Too many sign-in attempts. Try again in 15 minutes.", 429
                    )
                elif (
                    user
                    and (
                        path in {"/scan", "/ai-review"}
                        or path.startswith("/sources/")
                        or path in {"/docs", "/redoc", "/openapi.json"}
                    )
                    and not user.is_admin
                ):
                    response = error_page(
                        "This action requires a beta administrator.", 403
                    )
                elif (
                    user
                    and path == "/settings/password"
                    and not take_limit(f"password:user:{user.user_id}", 10)
                ):
                    response = error_page(
                        "Too many password changes. Try again later.", 429
                    )
                elif (
                    user
                    and (
                        path in {"/analyze", "/scan-source"}
                        or path.endswith("/readiness/analyze")
                        or path.endswith("/refresh")
                    )
                    and not take_limit(f"operation:user:{user.user_id}", 10)
                ):
                    response = error_page(
                        "Your hourly analysis allowance has been reached. Try again later.",
                        429,
                    )
                else:
                    response = await call_next(request)
            elif (
                user
                and path in {"/docs", "/redoc", "/openapi.json"}
                and not user.is_admin
            ):
                response = error_page("This action requires a beta administrator.", 403)
            else:
                response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
            )
            if secure_cookies():
                response.headers["Strict-Transport-Security"] = "max-age=31536000"
            response.set_cookie(
                CSRF_COOKIE,
                csrf,
                httponly=True,
                secure=secure_cookies(),
                samesite="strict",
                path="/",
            )
            return response
        finally:
            principal.reset(scope_token)
            ai_attempts.reset(budget_token)


def install_error_handlers(app) -> None:
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException
    from sqlalchemy.exc import SQLAlchemyError

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        # Never echo credential or notice input from Pydantic validation errors.
        return error_page(
            "Some form values are missing or invalid. Go back, check the required fields and try again.",
            422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_page(str(exc.detail), exc.status_code)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, _exc: SQLAlchemyError):
        logging.getLogger(__name__).error(
            "Beta operation could not reach persistent storage"
        )
        return error_page(
            "The service is temporarily unavailable. Please retry later.", 503
        )
