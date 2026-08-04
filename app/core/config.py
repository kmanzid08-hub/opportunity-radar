from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache


DEFAULT_DATABASE_URL = "sqlite:///./opportunities.db"
DEFAULT_ENABLE_INTERNAL_SCHEDULER = True
DEFAULT_INBOX_NO_DEADLINE_MAX_AGE_DAYS = 45

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class SettingsError(ValueError):
    """Raised when application configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    enable_internal_scheduler: bool
    inbox_no_deadline_max_age_days: int


def _normalise_database_url(value: str) -> str:
    if value.startswith("postgres://"):
        return value.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )

    if value.startswith("postgresql://"):
        return value.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )

    return value


def _parse_boolean(value: str, *, setting_name: str) -> bool:
    normalised = value.strip().lower()

    if normalised in TRUE_VALUES:
        return True

    if normalised in FALSE_VALUES:
        return False

    accepted = ", ".join(
        sorted(TRUE_VALUES | FALSE_VALUES)
    )
    raise SettingsError(
        f"{setting_name} must be one of: {accepted}; "
        f"received {value!r}."
    )


def _parse_positive_integer(
    value: str,
    *,
    setting_name: str,
) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise SettingsError(
            f"{setting_name} must be a positive integer; "
            f"received {value!r}."
        ) from exc

    if parsed < 1:
        raise SettingsError(
            f"{setting_name} must be a positive integer; "
            f"received {value!r}."
        )

    return parsed


def load_settings(
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Build settings from an explicit mapping or the process environment."""
    source = os.environ if environ is None else environ

    database_url = source.get(
        "DATABASE_URL",
        DEFAULT_DATABASE_URL,
    )
    scheduler_value = source.get(
        "ENABLE_INTERNAL_SCHEDULER",
        str(DEFAULT_ENABLE_INTERNAL_SCHEDULER),
    )
    no_deadline_max_age_value = source.get(
        "INBOX_NO_DEADLINE_MAX_AGE_DAYS",
        str(DEFAULT_INBOX_NO_DEADLINE_MAX_AGE_DAYS),
    )

    return Settings(
        database_url=_normalise_database_url(
            database_url
        ),
        enable_internal_scheduler=_parse_boolean(
            scheduler_value,
            setting_name="ENABLE_INTERNAL_SCHEDULER",
        ),
        inbox_no_deadline_max_age_days=(
            _parse_positive_integer(
                no_deadline_max_age_value,
                setting_name=(
                    "INBOX_NO_DEADLINE_MAX_AGE_DAYS"
                ),
            )
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the canonical immutable settings for this process."""
    return load_settings()


def clear_settings_cache() -> None:
    """Clear cached settings so tests can apply isolated overrides."""
    get_settings.cache_clear()
