"""Regression tests for the recurring production login CSRF mismatch."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.testclient import TestClient
import pytest

from app import beta_security
from app.beta_security import BetaSecurityMiddleware, CSRF_COOKIE, SESSION_COOKIE


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(beta_security, "take_limit", lambda *a, **kw: True)
    app = FastAPI()
    app.add_middleware(BetaSecurityMiddleware)

    @app.get("/login")
    def login_page(request: Request):
        return HTMLResponse(request.state.csrf)

    @app.post("/login")
    def login_post():
        return PlainTextResponse("login handler reached")

    @app.get("/static/app.js")
    def static_asset():
        return PlainTextResponse("// static")

    @app.get("/healthz")
    def health():
        return PlainTextResponse("ok")

    with TestClient(app) as client:
        yield client


def test_first_login_issues_single_csrf_cookie(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert response.text == client.cookies.get(CSRF_COOKIE)
    assert response.headers.get("cache-control") == "no-store"


def test_static_asset_does_not_issue_csrf_cookie(client):
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert CSRF_COOKIE not in response.headers.get("set-cookie", "")
    assert client.cookies.get(CSRF_COOKIE) is None

    login = client.get("/login")
    token = login.text
    assert client.cookies.get(CSRF_COOKIE) == token

    static = client.get("/static/app.js")
    assert CSRF_COOKIE not in static.headers.get("set-cookie", "")
    assert client.cookies.get(CSRF_COOKIE) == token


def test_health_probe_never_sets_csrf_cookie(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert CSRF_COOKIE not in response.headers.get("set-cookie", "")


def test_form_survives_session_cookie_rotation(client):
    original_form = client.get("/login")
    token = original_form.text

    client.cookies.set(SESSION_COOKIE, "first-auth-session")
    assert client.get("/login").text == token

    client.cookies.set(SESSION_COOKIE, "rotated-auth-session")
    response = client.post(
        "/login",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    assert client.cookies.get(CSRF_COOKIE) == token
    assert CSRF_COOKIE not in response.headers.get("set-cookie", "")


def test_logout_style_session_deletion_does_not_rotate_csrf(client):
    token = client.get("/login").text
    client.cookies.set(SESSION_COOKIE, "some-auth-session")
    client.cookies.delete(SESSION_COOKIE)
    response = client.post("/login", data={"csrf_token": token})
    assert response.status_code == 200
    assert client.cookies.get(CSRF_COOKIE) == token


def test_missing_or_wrong_csrf_token_is_rejected(client):
    token = client.get("/login").text
    assert client.post("/login", data={"csrf_token": ""}).status_code == 403
    assert client.post("/login", data={"csrf_token": "wrong"}).status_code == 403
    assert client.post("/login", data={"csrf_token": "é"}).status_code == 403
    assert client.cookies.get(CSRF_COOKIE) == token


def test_cross_origin_submit_stays_rejected(client):
    token = client.get("/login").text
    response = client.post(
        "/login",
        data={"csrf_token": token},
        headers={"origin": "https://attacker.example", "sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403


def test_same_origin_submit_remains_valid(client):
    token = client.get("/login").text
    response = client.post(
        "/login",
        data={"csrf_token": token},
        headers={"origin": "http://testserver", "sec-fetch-site": "same-origin"},
    )
    assert response.status_code == 200
