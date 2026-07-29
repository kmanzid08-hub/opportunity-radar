from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "automation_state.json"

# Daily tender scan. This is your existing scanner command.
TENDER_SCAN_COMMAND = os.getenv(
    "TENDER_SCAN_COMMAND",
    f'"{sys.executable}" -m app.scanner',
)

# Website discovery. Change this environment variable only if your existing
# discovery module has a different name.
SOURCE_DISCOVERY_COMMAND = os.getenv(
    "SOURCE_DISCOVERY_COMMAND",
    f'"{sys.executable}" -m app.source_discovery',
)

TENDER_INTERVAL = timedelta(days=1)
DISCOVERY_INTERVAL = timedelta(days=5)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, str]) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_due(last_run: datetime | None, interval: timedelta, now: datetime) -> bool:
    return last_run is None or now >= last_run + interval


def run_command(label: str, command: str) -> bool:
    print(f"\n{'=' * 60}")
    print(label)
    print(f"Command: {command}")
    print(f"{'=' * 60}")

    try:
        completed = subprocess.run(
            shlex.split(command, posix=os.name != "nt"),
            check=False,
        )
    except OSError as exc:
        print(f"Could not start {label}: {exc}")
        return False

    if completed.returncode != 0:
        print(f"{label} failed with exit code {completed.returncode}")
        return False

    print(f"{label} completed successfully")
    return True


def main() -> int:
    now = utc_now()
    state = load_state()

    last_tender_scan = parse_timestamp(state.get("last_tender_scan"))
    last_source_discovery = parse_timestamp(state.get("last_source_discovery"))

    anything_ran = False
    overall_success = True

    if is_due(last_tender_scan, TENDER_INTERVAL, now):
        anything_ran = True
        if run_command("Daily tender scan", TENDER_SCAN_COMMAND):
            state["last_tender_scan"] = utc_now().isoformat()
            save_state(state)
        else:
            overall_success = False
    else:
        next_run = last_tender_scan + TENDER_INTERVAL
        print(f"Daily tender scan is not due. Next due: {next_run.isoformat()}")

    if is_due(last_source_discovery, DISCOVERY_INTERVAL, now):
        anything_ran = True
        if run_command("Five-day website discovery", SOURCE_DISCOVERY_COMMAND):
            state["last_source_discovery"] = utc_now().isoformat()
            save_state(state)
        else:
            overall_success = False
    else:
        next_run = last_source_discovery + DISCOVERY_INTERVAL
        print(f"Website discovery is not due. Next due: {next_run.isoformat()}")

    if not anything_ran:
        print("No automation task was due.")

    return 0 if overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
