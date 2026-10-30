"""Run as: python -m scripts.controlled_live_test --database opportunities.db.

Default is read-only preflight. Network/AI/local business writes require --execute.
No migrations, scheduling, full scans, fixtures, or production connections here.
"""

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def local_database_path(value: str) -> Path:
    if value.startswith(("\\\\", "//")) or "://" in value:
        raise ValueError("Pass a local development SQLite file, not a database URL")
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError(
            "Database must be an existing local file within the repository"
        )
    return path


def main(*, targeted: bool = False) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--country")
    parser.add_argument("--company-id", type=int, action="append")
    parser.add_argument("--max-sources", type=int, default=3)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-companies", type=int, default=2)
    parser.add_argument("--max-ai-calls", type=int, default=20)
    if targeted:
        parser.add_argument("--url", action="append", required=True)
    args = parser.parse_args()
    try:
        path = local_database_path(args.database)
        if "app.database" in sys.modules:
            raise ValueError("Invoke the runner in a fresh process")
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
        if (
            os.environ.get("RENDER")
            or os.environ.get("APP_ENV", "").lower() == "production"
        ):
            raise ValueError("Development runner refuses a production environment")
        # Process-only settings; never write .env or alter application defaults.
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{path.as_posix()}"
        os.environ["ENABLE_INTERNAL_SCHEDULER"] = "false"
        if not os.environ.get("BRAVE_API_KEY") and os.environ.get(
            "BRAVE_SEARCH_API_KEY"
        ):
            os.environ["BRAVE_API_KEY"] = os.environ["BRAVE_SEARCH_API_KEY"]
        from app.core.config import clear_settings_cache

        clear_settings_cache()
        from app.schema_status import SchemaState, inspect_schema
        from app.controlled_runner import RunLimits, run_controlled, selected_profiles

        limits = RunLimits(
            args.max_sources, args.max_pages, args.max_companies, args.max_ai_calls
        )
        if targeted:
            from app.targeted_runner import validate_targets

            validate_targets(args.url, limits.pages)
            if limits.ai_calls > 20 or args.country:
                raise ValueError("Targeted replay uses at most 20 AI attempts and notice-derived country")
        status = inspect_schema(os.environ["DATABASE_URL"])
        print(f"PREFLIGHT schema={status.state.value}; head={status.expected_revision}")
        blockers = []
        if status.state is not SchemaState.CURRENT:
            blockers.append(
                "Local schema is not current; apply Alembic migrations separately"
            )
        else:
            profiles = selected_profiles(limits, args.company_id)
            print(f"PREFLIGHT existing active companies selected={len(profiles)}")
        for key in (("GEMINI_API_KEY",) if targeted else ("GEMINI_API_KEY", "BRAVE_API_KEY")):
            present = bool(os.environ.get(key, "").strip())
            print(f"PREFLIGHT {key} configured={present}")
            if not present:
                blockers.append(f"{key} is not configured")
        if blockers:
            for blocker in blockers:
                print(f"BLOCKED: {blocker}")
            print("LIVE TEST NOT EXECUTED")
            return 2
        if not args.execute:
            print(
                "Preflight complete. Add --execute to explicitly permit bounded network/AI/local writes."
            )
            return 0
        from google import genai
        from google.genai import types
        from app.intelligence.gemini_client import GeminiClient

        def single_attempt_client(**kwargs):
            kwargs["http_options"] = types.HttpOptions(
                timeout=30_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            )
            return genai.Client(**kwargs)

        backend = GeminiClient.from_environment(client_factory=single_attempt_client)
        if targeted:
            from app.targeted_runner import run_targeted

            targeted_report = run_targeted(
                backend=backend, urls=args.url, limits=limits, company_ids=args.company_id,
            )
            return 0 if targeted_report["run_success"] else 1
        report = run_controlled(
            backend=backend,
            limits=limits,
            country=args.country,
            company_ids=args.company_id,
        )
        return 0 if report.run_success else 1
    except Exception as exc:
        # Only our safe validation messages may be displayed; never provider text.
        print(
            f"Runner stopped: {str(exc) if isinstance(exc, ValueError) else type(exc).__name__}"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
