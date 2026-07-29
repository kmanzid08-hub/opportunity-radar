from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.database import engine
from app.source_discovery import run_source_discovery

JOB_NAME = "website_discovery"
INTERVAL = timedelta(days=5)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_state_table() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS automation_job_state (
                    job_name VARCHAR(100) PRIMARY KEY,
                    last_success_at TIMESTAMP WITH TIME ZONE
                )
                """
            )
        )


def _last_success() -> datetime | None:
    with engine.begin() as connection:
        value = connection.execute(
            text(
                """
                SELECT last_success_at
                FROM automation_job_state
                WHERE job_name = :job_name
                """
            ),
            {"job_name": JOB_NAME},
        ).scalar_one_or_none()

    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _record_success(completed_at: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO automation_job_state (job_name, last_success_at)
                VALUES (:job_name, :completed_at)
                ON CONFLICT (job_name)
                DO UPDATE SET last_success_at = EXCLUDED.last_success_at
                """
            ),
            {
                "job_name": JOB_NAME,
                "completed_at": completed_at,
            },
        )


def main() -> int:
    _ensure_state_table()
    now = _utc_now()
    last_success = _last_success()

    if last_success is not None and now < last_success + INTERVAL:
        next_run = last_success + INTERVAL
        print(f"Website discovery is not due. Next due: {next_run.isoformat()}")
        return 0

    print("Website discovery is due. Starting discovery...")
    result = run_source_discovery()
    _record_success(_utc_now())
    print(f"Website discovery completed successfully: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
