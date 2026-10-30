"""SOURCE-only isolation: python -m scripts.gemini_source_diagnostic.

Default: one synthetic SOURCE request, no webpage fetch. --real-url URL makes
one separate SOURCE request using one fetched public page, only when explicitly
authorized after synthetic success. No discovery, persistence, or downstream AI.
"""

import argparse
from collections.abc import Callable
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.intelligence.gemini_client import (
    ClientFactory,
    GeminiClient,
    GeminiConfig,
    GeminiError,
    load_gemini_config,
)
from app.intelligence.gemini_diagnostics import safe_model_name
from app.intelligence.source_analyzer import MAX_SOURCE_CONTENT_CHARS, analyze_source
from app.intelligence.source_request_diagnostics import (
    source_failure_details,
    source_request_metadata,
)


SYNTHETIC_CONTENT = (
    "Title: Procurement Notices\nOrganisation: Example Government Agency\n"
    "Country: Rwanda\nThis is a government procurement page containing public "
    "procurement notices."
)


def run_source_diagnostic(
    config: GeminiConfig,
    *,
    page_content: str = SYNTHETIC_CONTENT,
    url: str = "https://example.invalid/procurement",
    country: str = "Rwanda",
    synthetic: bool = True,
    client_factory: ClientFactory | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    emit(f"key configured: {'yes' if config.is_configured else 'no'}")
    emit(f"model configured: {safe_model_name(config.model, config.api_key)}")
    emit(f"source mode: {'synthetic' if synthetic else 'single real page'}")
    attempted = False
    sdk_client = None
    failure: dict[str, Any] | None = None

    def factory(**kwargs):
        nonlocal sdk_client
        kwargs["http_options"] = types.HttpOptions(
            timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1)
        )
        sdk_client = (client_factory or genai.Client)(**kwargs)

        def generate_content(**request):
            nonlocal attempted, failure
            if attempted:
                raise RuntimeError("Only one SOURCE request permitted")
            metadata = source_request_metadata(
                model=request["model"],
                api_key=config.api_key,
                prompt=request["contents"],
                config=request["config"],
                content_chars=len(page_content[:MAX_SOURCE_CONTENT_CHARS]),
            )
            emit("SOURCE request metadata: " + json.dumps(metadata, sort_keys=True))
            attempted = True
            emit("Gemini request: SOURCE")
            try:
                return sdk_client.models.generate_content(**request)
            except Exception as exc:
                failure = source_failure_details(exc)
                raise

        return SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content)
        )

    try:
        result = analyze_source(
            url=url,
            page_content=page_content,
            source_name_hint="Example Government Agency" if synthetic else None,
            country_hint=country,
            client=GeminiClient(config, client_factory=factory, max_attempts=1),
        )
        emit(f"SOURCE decision={result.decision.value} confidence={result.confidence}")
    except Exception as exc:
        if failure is None:
            if isinstance(exc, GeminiError) and exc.diagnostic:
                failure = {
                    "category": exc.diagnostic.category,
                    "http_status": exc.diagnostic.http_status,
                }
            else:
                failure = source_failure_details(exc)
    finally:
        if sdk_client is not None:
            try:
                sdk_client.close()
            except Exception:
                pass
    emit(f"request attempted: {'yes' if attempted else 'no'}")
    emit(f"result: {'failure' if failure else 'success'}")
    if failure:
        emit("SOURCE error: " + json.dumps(failure, sort_keys=True))
    else:
        emit("http_status: 200; valid SOURCE structured response")
    return 1 if failure else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-url")
    parser.add_argument("--country", default="Rwanda")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    config = load_gemini_config()
    if not args.real_url:
        return run_source_diagnostic(config)
    # Imported only for an explicitly authorized single-page follow-up.
    from app.controlled_network import BoundedWeb

    web = BoundedWeb(sources=1, pages=0)
    try:
        content = web.source_text(args.real_url)
    except Exception:
        print("Single-page fetch failed; Gemini request attempted: no")
        return 1
    finally:
        web.session.close()
    return run_source_diagnostic(
        config,
        page_content=content,
        url=args.real_url,
        country=args.country,
        synthetic=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
