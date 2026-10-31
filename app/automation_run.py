"""Run Brave source discovery and AI matching as a separate scheduled worker.

This job must NOT run inside the FastAPI web process. It uses the existing
SourceDiscovery (Brave -> AI source assessment -> persistent Source records)
and run_scanner (stored Source pages -> AI notices -> company matching/triage).
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os

from sqlalchemy import text

from app.database import engine
from app.models import Source
from app.scanner import run_scanner
from app.source_discovery import SourceDiscovery

logger = logging.getLogger("opportunity_radar.automation")

# PostgreSQL advisory lock prevents concurrent worker runs sharing this DB.
LOCK_ID = 671039102


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        raise RuntimeError("Scheduled discovery requires the shared PostgreSQL DATABASE_URL")
    if not os.environ.get("BRAVE_API_KEY", "").strip():
        raise RuntimeError("BRAVE_API_KEY is required for source discovery")
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise RuntimeError("GEMINI_API_KEY is required for AI evaluation")

    with engine.connect() as lock_conn:
        locked = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": LOCK_ID}
        ).scalar_one()
        lock_conn.commit()
        if not locked:
            logger.info("Another Opportunity Radar automation run is active; skipping")
            return
        try:
            from sqlalchemy import select, func
            with engine.connect() as conn:
                source_count = conn.execute(
                    select(func.count()).select_from(Source)
                ).scalar_one()

            now = datetime.now(timezone.utc)
            mode = os.environ.get("AUTOMATION_MODE", "scheduled").strip().lower()
            should_discover = (
                mode in {"all", "discover"}
                or (mode == "scheduled" and (source_count == 0 or now.date().toordinal() % 5 == 0))
            )
            should_scan = mode in {"all", "scan", "scheduled"}
            if mode not in {"all", "discover", "scan", "scheduled"}:
                raise ValueError("Invalid AUTOMATION_MODE")

            if should_discover:
                logger.info("Starting Brave source discovery for Rwanda")
                summary = SourceDiscovery(country="Rwanda").run()
                logger.info(
                    "Discovery finished: new=%s accepted=%s queries=%s failures=%s",
                    summary["added_sources"], summary["accepted_sources"],
                    summary["queries_executed"], summary["failed_queries"],
                )
            if should_scan:
                logger.info("Scanning saved sources and evaluating business matches")
                run_scanner()
                logger.info("Opportunity scanning and matching finished")
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": LOCK_ID})
            lock_conn.commit()


if __name__ == "__main__":
    main()
