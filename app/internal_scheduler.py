from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from app.jobs import run_tender_scan, run_website_discovery

logger = logging.getLogger("opportunity_radar.scheduler")

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = BASE_DIR / "automation_state.json"
LOCK_FILE = BASE_DIR / "automation_scheduler.lock"
LOG_FILE = BASE_DIR / "automation.log"

CHECK_INTERVAL_SECONDS = 60
TENDER_INTERVAL = timedelta(days=1)
DISCOVERY_INTERVAL = timedelta(days=5)
FAILURE_RETRY_INTERVAL = timedelta(hours=6)


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    interval: timedelta
    function: Callable[[], None]


JOBS = (
    ScheduledJob("tender_scan", TENDER_INTERVAL, run_tender_scan),
    ScheduledJob(
        "website_discovery",
        DISCOVERY_INTERVAL,
        run_website_discovery,
    ),
)


class InternalScheduler:
    """Small persistent scheduler that runs while the FastAPI app is open."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._job_lock = threading.Lock()
        self._owns_process_lock = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        if not self._acquire_process_lock():
            logger.warning(
                "Another Opportunity Radar scheduler already appears to be "
                "running. This process will not start a second scheduler."
            )
            return

        self._configure_logging()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="opportunity-radar-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("Internal scheduler started")

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=10)

        self._release_process_lock()
        logger.info("Internal scheduler stopped")

    def _run_loop(self) -> None:
        # Check immediately when the application opens, then once per minute.
        while not self._stop_event.is_set():
            try:
                self._run_due_jobs()
            except Exception:
                logger.exception("Unexpected scheduler error")

            self._stop_event.wait(CHECK_INTERVAL_SECONDS)

    def _run_due_jobs(self) -> None:
        if not self._job_lock.acquire(blocking=False):
            return

        try:
            state = self._load_state()
            now = datetime.now(timezone.utc)

            for job in JOBS:
                job_state = state.get(job.name, {})

                if not self._is_due(job, job_state, now):
                    continue

                self._run_job(job, state)
        finally:
            self._job_lock.release()

    def _is_due(
        self,
        job: ScheduledJob,
        job_state: dict[str, str],
        now: datetime,
    ) -> bool:
        last_success = self._parse_datetime(job_state.get("last_success"))
        last_failure = self._parse_datetime(job_state.get("last_failure"))

        if last_success is None:
            if last_failure is None:
                return True
            return now - last_failure >= FAILURE_RETRY_INTERVAL

        due_at = last_success + job.interval

        if last_failure is not None and last_failure > last_success:
            retry_at = last_failure + FAILURE_RETRY_INTERVAL
            return now >= min(due_at, retry_at)

        return now >= due_at

    def _run_job(
        self,
        job: ScheduledJob,
        state: dict[str, dict[str, str]],
    ) -> None:
        started_at = datetime.now(timezone.utc)
        logger.info("Job %s started", job.name)

        state.setdefault(job.name, {})
        state[job.name]["last_started"] = started_at.isoformat()
        state[job.name]["status"] = "running"
        self._save_state(state)

        try:
            job.function()
        except Exception as exc:
            failed_at = datetime.now(timezone.utc)
            state[job.name]["last_failure"] = failed_at.isoformat()
            state[job.name]["status"] = "failed"
            state[job.name]["last_error"] = f"{type(exc).__name__}: {exc}"[:1000]
            self._save_state(state)
            logger.exception("Job %s failed", job.name)
            return

        completed_at = datetime.now(timezone.utc)
        state[job.name]["last_success"] = completed_at.isoformat()
        state[job.name]["status"] = "completed"
        state[job.name]["last_error"] = ""
        state[job.name]["duration_seconds"] = str(
            round((completed_at - started_at).total_seconds(), 3)
        )
        self._save_state(state)
        logger.info("Job %s completed", job.name)

    def _load_state(self) -> dict[str, dict[str, str]]:
        if not STATE_FILE.exists():
            return {}

        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read automation state; starting fresh")
            return {}

        return data if isinstance(data, dict) else {}

    def _save_state(self, state: dict[str, dict[str, str]]) -> None:
        temporary_file = STATE_FILE.with_suffix(".tmp")
        temporary_file.write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_file.replace(STATE_FILE)

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    def _acquire_process_lock(self) -> bool:
        try:
            descriptor = os.open(
                LOCK_FILE,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            try:
                pid_text = LOCK_FILE.read_text(encoding="utf-8").strip()
                existing_pid = int(pid_text)
                os.kill(existing_pid, 0)
                return False
            except (OSError, ValueError):
                try:
                    LOCK_FILE.unlink()
                except OSError:
                    return False
                return self._acquire_process_lock()

        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(str(os.getpid()))

        self._owns_process_lock = True
        return True

    def _release_process_lock(self) -> None:
        if not self._owns_process_lock:
            return

        try:
            LOCK_FILE.unlink(missing_ok=True)
        finally:
            self._owns_process_lock = False

    @staticmethod
    def _configure_logging() -> None:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        absolute_log_file = str(LOG_FILE.resolve())

        for handler in root_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                if handler.baseFilename == absolute_log_file:
                    return

        file_handler = logging.FileHandler(
            LOG_FILE,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
        )
        root_logger.addHandler(file_handler)


scheduler = InternalScheduler()
