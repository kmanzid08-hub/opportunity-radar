from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from app.scanner import run_scanner

logger = logging.getLogger("opportunity_radar.jobs")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_tender_scan() -> None:
    """Run the normal opportunity/tender scan."""
    logger.info("Starting daily tender scan")
    run_scanner()
    logger.info("Daily tender scan completed")


def run_website_discovery() -> None:
    """
    Run the crawler that discovers and adds new organisation websites.

    The discovery crawler must be available as ``app.discover_sources`` and
    must be runnable with ``python -m app.discover_sources``.
    """
    logger.info("Starting five-day website discovery")

    command = [sys.executable, "-m", "app.discover_sources"]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    if completed.stdout:
        logger.info("Website discovery output:\n%s", completed.stdout.rstrip())

    if completed.stderr:
        logger.warning("Website discovery errors:\n%s", completed.stderr.rstrip())

    if completed.returncode != 0:
        raise RuntimeError(
            "Website discovery failed with exit code "
            f"{completed.returncode}. Confirm that app/discover_sources.py "
            "exists and can run with: python -m app.discover_sources"
        )

    logger.info("Five-day website discovery completed")
