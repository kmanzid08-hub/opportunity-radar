"""One trivial Gemini request, no retries or database/application startup.

Run explicitly: python -m scripts.gemini_diagnostic
"""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, field_validator

from app.intelligence.gemini_client import (
    ClientFactory,
    GeminiClient,
    GeminiConfig,
    GeminiError,
    load_gemini_config,
)
from app.intelligence.gemini_diagnostics import (
    GeminiDiagnostic,
    classify_gemini_error,
    safe_model_name,
)


class ConnectionCheck(BaseModel):
    ok: bool
    message: Literal["Gemini connection successful"]

    @field_validator("ok")
    @classmethod
    def require_success(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Expected a successful connection response")
        return value


def run_diagnostic(
    config: GeminiConfig,
    *,
    client_factory: ClientFactory | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    emit(f"key configured: {'yes' if config.is_configured else 'no'}")
    emit(f"model configured: {safe_model_name(config.model, config.api_key)}")
    attempted = False
    sdk_client = None

    def one_attempt_factory(**kwargs):
        nonlocal sdk_client
        kwargs["http_options"] = types.HttpOptions(
            timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1)
        )
        sdk_client = (client_factory or genai.Client)(**kwargs)

        def generate_content(**request):
            nonlocal attempted
            if attempted:
                raise RuntimeError("Diagnostic permits only one request")
            attempted = True
            return sdk_client.models.generate_content(**request)

        return SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content)
        )

    diagnostic = None
    try:
        client = GeminiClient(
            config, client_factory=one_attempt_factory, max_attempts=1
        )
        client.generate_structured(
            'Return only {"ok": true, "message": "Gemini connection successful"}.',
            ConnectionCheck,
        )
    except GeminiError as exc:
        diagnostic = exc.diagnostic or GeminiDiagnostic("UNKNOWN_PROVIDER_ERROR")
    except Exception as exc:
        diagnostic = classify_gemini_error(exc)
    finally:
        if sdk_client is not None:
            try:
                sdk_client.close()
            except Exception:
                pass  # Never print SDK cleanup exceptions or retry a request.
    emit(f"request attempted: {'yes' if attempted else 'no'}")
    emit(f"result: {'failure' if diagnostic else 'success'}")
    emit(f"category: {diagnostic.category if diagnostic else 'NONE'}")
    emit(f"http_status: {diagnostic.http_status if diagnostic else 200}")
    if diagnostic:
        emit(f"retryable: {str(diagnostic.retryable).lower()}")
    return 1 if diagnostic else 0


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    return run_diagnostic(load_gemini_config())


if __name__ == "__main__":
    raise SystemExit(main())
