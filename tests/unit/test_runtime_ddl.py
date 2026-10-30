from __future__ import annotations

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "app"
FORBIDDEN_SCHEMA_MUTATIONS = re.compile(
    r"Base\.metadata\.create_all|create_all\s*\(|"
    r"CREATE\s+TABLE|ALTER\s+TABLE|PRAGMA\s+table_info|"
    r"DROP\s+TABLE|ADD\s+COLUMN",
    re.IGNORECASE,
)


def test_application_contains_no_runtime_schema_mutation() -> None:
    violations: list[str] = []

    for path in APP_ROOT.rglob("*.py"):
        contents = path.read_text(encoding="utf-8")
        if FORBIDDEN_SCHEMA_MUTATIONS.search(contents):
            violations.append(str(path.relative_to(PROJECT_ROOT)))

    assert violations == []


def test_ad_hoc_migration_modules_are_retired() -> None:
    assert list(APP_ROOT.glob("migrate_*.py")) == []
