"""All Jinja templates must compile: a malformed tag can cause HTTP 500 after login."""
from pathlib import Path
import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


@pytest.mark.parametrize(
    "template_path",
    sorted(p.relative_to(TEMPLATES).as_posix() for p in TEMPLATES.rglob("*.html")),
)
def test_template_compiles(template_path: str) -> None:
    environment = Environment(loader=FileSystemLoader(str(TEMPLATES)))
    assert environment.get_template(template_path) is not None


def test_dashboard_requires_two_balanced_empty_state_closures() -> None:
    content = (TEMPLATES / "index.html").read_text()
    assert "{% if not business_profile_active %}" in content
    assert "{% endif %}" in content
    assert "{% endif %>" not in content
