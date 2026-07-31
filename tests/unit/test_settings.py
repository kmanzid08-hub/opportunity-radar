from __future__ import annotations

import builtins
import importlib.util
import sys
from unittest.mock import Mock

import pytest

from app.core import config
from app.core.config import (
    SettingsError,
    clear_settings_cache,
    get_settings,
    load_settings,
)


def test_default_settings_preserve_current_behavior() -> None:
    settings = load_settings({})

    assert settings.database_url == "sqlite:///./opportunities.db"
    assert settings.enable_internal_scheduler is True


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("sqlite+pysqlite:///:memory:", "sqlite+pysqlite:///:memory:"),
        (
            "postgres://user:pass@example.test/radar",
            "postgresql+psycopg://user:pass@example.test/radar",
        ),
        (
            "postgresql://user:pass@example.test/radar",
            "postgresql+psycopg://user:pass@example.test/radar",
        ),
        (
            " sqlite:///./custom.db ",
            " sqlite:///./custom.db ",
        ),
    ],
)
def test_database_url_override_and_compatibility(
    configured: str,
    expected: str,
) -> None:
    settings = load_settings(
        {
            "DATABASE_URL": configured,
            "ENABLE_INTERNAL_SCHEDULER": "false",
        }
    )

    assert settings.database_url == expected


def test_repeated_settings_creation_is_deterministic() -> None:
    environ = {
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "ENABLE_INTERNAL_SCHEDULER": "off",
    }

    assert load_settings(environ) == load_settings(environ)


@pytest.mark.parametrize(
    "value",
    ["1", "true", "yes", "on", " TRUE ", "Yes", "\tON\n"],
)
def test_scheduler_true_variants(value: str) -> None:
    assert load_settings(
        {"ENABLE_INTERNAL_SCHEDULER": value}
    ).enable_internal_scheduler is True


@pytest.mark.parametrize(
    "value",
    ["0", "false", "no", "off", " FALSE ", "No", "\tOFF\n"],
)
def test_scheduler_false_variants(value: str) -> None:
    assert load_settings(
        {"ENABLE_INTERNAL_SCHEDULER": value}
    ).enable_internal_scheduler is False


def test_invalid_scheduler_value_fails_clearly() -> None:
    with pytest.raises(
        SettingsError,
        match="ENABLE_INTERNAL_SCHEDULER must be one of",
    ):
        load_settings(
            {"ENABLE_INTERNAL_SCHEDULER": "sometimes"}
        )


def test_process_environment_override_is_cacheable_and_resettable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "sqlite+pysqlite:///:memory:",
    )
    monkeypatch.setenv(
        "ENABLE_INTERNAL_SCHEDULER",
        "off",
    )
    clear_settings_cache()

    try:
        first = get_settings()
        second = get_settings()

        assert first is second
        assert first.database_url == "sqlite+pysqlite:///:memory:"
        assert first.enable_internal_scheduler is False
    finally:
        clear_settings_cache()


def test_importing_settings_has_no_external_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler_start = Mock()
    scheduler_module = sys.modules.get(
        "app.internal_scheduler"
    )

    if scheduler_module is not None:
        monkeypatch.setattr(
            scheduler_module.scheduler,
            "start",
            scheduler_start,
        )

    original_import = builtins.__import__
    forbidden_imports = (
        "app.database",
        "app.internal_scheduler",
        "requests",
        "sqlalchemy",
    )

    def guarded_import(
        name: str,
        *args: object,
        **kwargs: object,
    ):
        if name.startswith(forbidden_imports):
            raise AssertionError(
                f"Settings imported external runtime module: {name}"
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(
        builtins,
        "__import__",
        guarded_import,
    )

    module_name = "_opportunity_radar_settings_import_test"
    specification = importlib.util.spec_from_file_location(
        module_name,
        config.__file__,
    )
    assert specification is not None
    assert specification.loader is not None

    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module

    try:
        specification.loader.exec_module(module)
        loaded = module.load_settings({})
    finally:
        sys.modules.pop(module_name, None)

    assert loaded.database_url == "sqlite:///./opportunities.db"
    scheduler_start.assert_not_called()
