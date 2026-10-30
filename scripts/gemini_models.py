"""Read-only model discovery: python -m scripts.gemini_models.

Uses only models.list (including pagination), never generation or database access.
The SDK exposes generation actions, not a structured-output capability flag.
Confirm JSON-schema support in official model documentation before selection.
"""

from collections.abc import Callable
from pathlib import Path
import re

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.intelligence.gemini_client import (
    ClientFactory,
    GeminiConfig,
    load_gemini_config,
)
from app.intelligence.gemini_diagnostics import classify_gemini_error, safe_model_name


MAX_PAGES = 10
PAGE_SIZE = 1000


def generation_candidate(model: types.Model, api_key: str) -> str | None:
    name = model.name
    if not name or safe_model_name(name, api_key) != name:
        return None
    if not re.fullmatch(r"models/gemini-[A-Za-z0-9._-]+", name):
        return None
    # Specialized output models are not candidates for ordinary JSON analysis.
    if set(name.lower().split("-")) & {
        "image",
        "audio",
        "tts",
        "live",
        "robotics",
        "embedding",
    }:
        return None
    if "generateContent" not in (model.supported_actions or []):
        return None
    return name


def discover_models(
    config: GeminiConfig,
    *,
    client_factory: ClientFactory | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    if not config.is_configured:
        emit("Model discovery failed category=AUTHENTICATION_ERROR http_status=unknown")
        return 1
    client = None
    candidates: set[str] = set()
    complete = False
    try:
        client = (client_factory or genai.Client)(
            api_key=config.api_key,
            vertexai=False,
            http_options=types.HttpOptions(
                timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )
        pager = client.models.list(
            config=types.ListModelsConfig(page_size=PAGE_SIZE, query_base=True)
        )
        for page_number in range(MAX_PAGES):
            for model in pager.page:
                name = generation_candidate(model, config.api_key or "")
                if name:
                    candidates.add(name)
            if not pager.config.get("page_token"):
                complete = True
                break
            if page_number + 1 < MAX_PAGES:
                pager.next_page()
    except Exception as exc:
        diagnostic = classify_gemini_error(exc)
        # A listing 404 is not evidence that an individual model is missing.
        category = (
            "UNKNOWN_PROVIDER_ERROR"
            if diagnostic.category == "MODEL_NOT_FOUND"
            else diagnostic.category
        )
        emit(
            f"Model discovery failed category={category} "
            f"http_status={diagnostic.http_status or 'unknown'}; listing incomplete"
        )
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass  # Never print provider cleanup payloads.
    for name in sorted(candidates):
        emit(f"model={name} capability=generateContent")
    emit(
        "Structured-output support is not exposed by models.list; "
        "confirm model documentation. No generation requests made."
    )
    if not complete:
        emit("Listing incomplete: pagination safety limit reached.")
        return 1
    if not candidates:
        emit("No general-purpose Gemini generateContent candidates were listed.")
    return 0


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    return discover_models(load_gemini_config())


if __name__ == "__main__":
    raise SystemExit(main())
