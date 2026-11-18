"""Client-facing redesign keeps important workflows accessible and secure."""
from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup


def test_discover_has_simple_first_view_and_all_existing_filters(client):
    response = client.get("/")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    assert soup.select_one("h1") is not None
    assert soup.select_one("#opportunities") is not None
    assert soup.select_one(".smart-filter-form input[name='q']") is not None
    assert soup.select_one(".smart-filter-form select[name='company_id']") is not None
    assert soup.select_one(".smart-filter-form select[name='deadline_filter']") is not None
    advanced = soup.select_one("details.advanced-filters")
    assert advanced is not None
    assert advanced.get("open") is None
    for name in ("country", "category", "match_strength", "status"):
        assert advanced.select_one(f"select[name='{name}']") is not None
    assert soup.select_one(".feed-health") is not None
    assert "Sources scanned today" in response.text
    assert "Scan success" in response.text
    assert "Create your first business profile" in response.text


def test_advanced_filters_remain_open_when_selected(client):
    response = client.get("/?country=Rwanda&status=New")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    assert soup.select_one("details.advanced-filters").has_attr("open")


def test_shared_navigation_is_same_across_pages(client):
    for url in ("/", "/pipeline", "/sources", "/business-profile"):
        response = client.get(url)
        assert response.status_code == 200
        soup = BeautifulSoup(response.text, "html.parser")
        assert soup.select_one(".brand[href='/']") is not None
        assert soup.select_one("a.skip-link[href='#main-content']") is not None
        assert soup.select_one("main#main-content") is not None
        assert soup.select_one("header.app-header") is not None


def test_redesign_has_accessible_motion_and_small_screen_layout():
    root = Path(__file__).resolve().parents[2]
    css = (root / "app/static/phase4.css").read_text()
    javascript = (root / "app/static/beta.js").read_text()
    assert "@media(max-width:720px)" in css
    assert "prefers-reduced-motion" in css
    assert "IntersectionObserver" in javascript
    assert 'event.key === "Escape"' in javascript
