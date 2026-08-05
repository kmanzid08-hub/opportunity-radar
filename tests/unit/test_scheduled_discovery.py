from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import scheduled_discovery
from app.core.config import SettingsError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _prepare_job(
    monkeypatch: pytest.MonkeyPatch,
    *,
    now: datetime,
    last_success: datetime | None,
) -> tuple[Mock, Mock, Mock]:
    ensure_state = Mock()
    run_discovery = Mock(return_value={"added_sources": 1})
    record_success = Mock()
    clock = Mock(
        side_effect=(now, now + timedelta(minutes=1))
    )

    monkeypatch.setattr(
        scheduled_discovery,
        "_ensure_state_table",
        ensure_state,
    )
    monkeypatch.setattr(
        scheduled_discovery,
        "_last_success",
        Mock(return_value=last_success),
    )
    monkeypatch.setattr(
        scheduled_discovery,
        "_utc_now",
        clock,
    )
    monkeypatch.setattr(
        scheduled_discovery,
        "run_source_discovery",
        run_discovery,
    )
    monkeypatch.setattr(
        scheduled_discovery,
        "_record_success",
        record_success,
    )

    return ensure_state, run_discovery, record_success


def test_scheduled_not_forced_and_not_due_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 5, 8, tzinfo=timezone.utc)
    monkeypatch.delenv(
        scheduled_discovery.FORCE_SETTING,
        raising=False,
    )
    ensure_state, run_discovery, record_success = _prepare_job(
        monkeypatch,
        now=now,
        last_success=now - timedelta(days=1),
    )

    assert scheduled_discovery.main() == 0
    ensure_state.assert_called_once_with()
    run_discovery.assert_not_called()
    record_success.assert_not_called()


def test_scheduled_not_forced_and_due_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 5, 8, tzinfo=timezone.utc)
    monkeypatch.setenv(
        scheduled_discovery.FORCE_SETTING,
        "false",
    )
    _, run_discovery, record_success = _prepare_job(
        monkeypatch,
        now=now,
        last_success=now - timedelta(days=6),
    )

    assert scheduled_discovery.main() == 0
    run_discovery.assert_called_once_with()
    record_success.assert_called_once_with(
        now + timedelta(minutes=1)
    )


def test_manual_forced_and_not_due_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 5, 8, tzinfo=timezone.utc)
    monkeypatch.setenv(
        scheduled_discovery.FORCE_SETTING,
        " YeS ",
    )
    _, run_discovery, _ = _prepare_job(
        monkeypatch,
        now=now,
        last_success=now - timedelta(minutes=5),
    )

    assert scheduled_discovery.main() == 0
    run_discovery.assert_called_once_with()


def test_forced_success_updates_last_run_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 5, 8, tzinfo=timezone.utc)
    completed_at = now + timedelta(minutes=1)
    monkeypatch.setenv(
        scheduled_discovery.FORCE_SETTING,
        "on",
    )
    _, _, record_success = _prepare_job(
        monkeypatch,
        now=now,
        last_success=now,
    )

    assert scheduled_discovery.main() == 0
    record_success.assert_called_once_with(completed_at)


@pytest.mark.parametrize(
    "value",
    ["1", "true", "yes", "on", " TRUE ", "\tOn\n"],
)
def test_force_true_values_are_supported(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(
        scheduled_discovery.FORCE_SETTING,
        value,
    )

    assert scheduled_discovery._force_discovery_enabled() is True


@pytest.mark.parametrize(
    "value",
    ["0", "false", "no", "off", " FALSE ", "\tOff\n"],
)
def test_force_false_values_are_supported(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(
        scheduled_discovery.FORCE_SETTING,
        value,
    )

    assert scheduled_discovery._force_discovery_enabled() is False


def test_invalid_force_value_fails_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        scheduled_discovery.FORCE_SETTING,
        "sometimes",
    )
    ensure_state = Mock()
    monkeypatch.setattr(
        scheduled_discovery,
        "_ensure_state_table",
        ensure_state,
    )

    with pytest.raises(
        SettingsError,
        match="FORCE_WEBSITE_DISCOVERY must be one of",
    ):
        scheduled_discovery.main()

    ensure_state.assert_not_called()


def test_workflow_force_flag_depends_on_dispatch_event() -> None:
    workflow = (
        PROJECT_ROOT
        / ".github"
        / "workflows"
        / "website-discovery.yml"
    ).read_text(encoding="utf-8")

    assert 'cron: "20 4 */5 * *"' in workflow
    assert "workflow_dispatch:" in workflow
    assert (
        "FORCE_WEBSITE_DISCOVERY: "
        "${{ github.event_name == 'workflow_dispatch' }}"
    ) in workflow
