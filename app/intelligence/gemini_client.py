from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import logging
import os
import random
import time
from typing import Any, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.intelligence.gemini_diagnostics import (
    GeminiDiagnostic,
    classify_gemini_error,
    safe_model_name,
)
from app.intelligence.gemini_schema import sanitize_gemini_schema


logger = logging.getLogger(__name__)


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_TIMEOUT_SECONDS = 60.0

StructuredResponse = TypeVar(
    "StructuredResponse",
    bound=BaseModel,
)
ClientFactory = Callable[..., Any]


class GeminiError(RuntimeError):
    """Base error raised by the Opportunity Radar Gemini boundary."""

    def __init__(self, message: str, *, diagnostic: GeminiDiagnostic | None = None):
        super().__init__(message)
        self.diagnostic = diagnostic


class GeminiConfigurationError(GeminiError):
    """Raised when Gemini is invoked without valid configuration."""


class GeminiAPIError(GeminiError):
    """Raised when the Gemini SDK cannot complete a generation request."""


class GeminiResponseError(GeminiError):
    """Raised when Gemini returns missing or invalid structured output."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic: GeminiDiagnostic | None = None,
        validation_details: list[dict[str, str]] | None = None,
    ):
        super().__init__(message, diagnostic=diagnostic)
        self.validation_details = validation_details or []


class GeminiRequestBudgetError(GeminiAPIError):
    """Local request budget refusal, not a provider outage."""


@dataclass(frozen=True, slots=True)
class GeminiConfig:
    api_key: str | None = field(default=None, repr=False)
    model: str = DEFAULT_GEMINI_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


def load_gemini_config(
    environ: Mapping[str, str] | None = None,
) -> GeminiConfig:
    """Load Gemini configuration without constructing an SDK client."""
    source = os.environ if environ is None else environ
    api_key = source.get("GEMINI_API_KEY", "").strip() or None
    model = (
        source.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    )
    return GeminiConfig(api_key=api_key, model=model)


class GeminiClient:
    """Lazy, testable wrapper around Google's official Gemini SDK."""

    def __init__(
        self,
        config: GeminiConfig,
        *,
        client_factory: ClientFactory | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], None] | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("Gemini max_attempts must be between 1 and 3")
        self.config = config
        self._client_factory = client_factory or genai.Client
        self._client: Any | None = None
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._jitter = jitter

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @staticmethod
    def _stage(operation: str) -> str:
        return {
            "source analysis": "SOURCE",
            "opportunity analysis": "OPPORTUNITY",
            "matching": "MATCH",
            "triage": "TRIAGE",
            "readiness": "READINESS",
            "requirement extraction": "READINESS",
            "connection diagnostic": "DIAGNOSTIC",
        }.get(operation, "GENERATION")

    def _log_attempt_failure(
        self, diagnostic: GeminiDiagnostic, operation: str, attempt: int, retrying: bool
    ) -> None:
        logger.warning(
            "Gemini %s attempt %s/%s failed category=%s http_status=%s model=%s retryable=%s retrying=%s",
            self._stage(operation),
            attempt,
            self.max_attempts,
            diagnostic.category,
            diagnostic.http_status if diagnostic.http_status is not None else "unknown",
            safe_model_name(self.config.model, self.config.api_key),
            str(diagnostic.retryable).lower(),
            str(retrying).lower(),
        )

    def _log_failure(self, diagnostic: GeminiDiagnostic, operation: str) -> None:
        logger.warning(
            "Gemini %s failed category=%s http_status=%s model=%s retryable=%s",
            operation,
            diagnostic.category,
            diagnostic.http_status if diagnostic.http_status is not None else "unknown",
            safe_model_name(self.config.model, self.config.api_key),
            str(diagnostic.retryable).lower(),
        )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        client_factory: ClientFactory | None = None,
        max_attempts: int = 3,
    ) -> GeminiClient:
        return cls(
            load_gemini_config(environ),
            client_factory=client_factory,
            max_attempts=max_attempts,
        )

    def _get_client(self) -> Any:
        if not self.config.api_key:
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is required to use Gemini intelligence.",
                diagnostic=GeminiDiagnostic("AUTHENTICATION_ERROR"),
            )

        if self._client is None:
            try:
                self._client = self._client_factory(
                    api_key=self.config.api_key,
                    http_options=types.HttpOptions(
                        timeout=int(self.config.timeout_seconds * 1000),
                        retry_options=types.HttpRetryOptions(attempts=1),
                    ),
                )
            except Exception as exc:
                raise GeminiConfigurationError(
                    "Gemini client initialization failed.",
                    diagnostic=classify_gemini_error(exc),
                ) from None

        return self._client

    def _generate(
        self,
        prompt: str,
        *,
        config: types.GenerateContentConfig,
        operation: str = "generation",
        before_attempt: Callable[[], None] | None = None,
    ) -> Any:
        if not prompt.strip():
            raise ValueError("Gemini prompt must not be blank.")

        try:
            sdk = self._get_client()
        except GeminiError as exc:
            self._log_failure(
                exc.diagnostic or GeminiDiagnostic("UNKNOWN_PROVIDER_ERROR"), operation
            )
            raise
        for attempt in range(1, self.max_attempts + 1):
            from app.beta_security import reserve_ai_attempt

            reserve_ai_attempt()
            # Budget refusals are not provider failures and must never be retried.
            if before_attempt is not None:
                before_attempt()
            try:
                result = sdk.models.generate_content(
                    model=self.config.model,
                    contents=prompt,
                    config=config,
                )
            except GeminiError as exc:
                diagnostic = exc.diagnostic or GeminiDiagnostic(
                    "UNKNOWN_PROVIDER_ERROR"
                )
                self._log_attempt_failure(diagnostic, operation, attempt, False)
                raise
            except Exception as exc:
                diagnostic = classify_gemini_error(exc)
                retrying = diagnostic.retryable and attempt < self.max_attempts
                self._log_attempt_failure(diagnostic, operation, attempt, retrying)
                if not retrying:
                    raise GeminiAPIError(
                        "Gemini generation failed.", diagnostic=diagnostic
                    ) from None
                jitter = self._jitter() if self._jitter else random.uniform(0, 0.25)
                delay = min(2.0, 2 ** (attempt - 1)) + max(0.0, min(0.25, jitter))
                (self._sleep or time.sleep)(delay)
            else:
                logger.info(
                    "Gemini %s attempt %s/%s succeeded",
                    self._stage(operation),
                    attempt,
                    self.max_attempts,
                )
                return result
        raise AssertionError("Gemini attempt limit exhausted")

    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
    ) -> str:
        try:
            response = self._generate(
                prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0,
                ),
            )
        except GeminiAPIError as exc:
            if exc.diagnostic is not None and exc.diagnostic.category == "QUOTA_EXCEEDED":
                from app.intelligence import openai_fallback

                if openai_fallback.fallback_enabled():
                    logger.warning("Gemini quota exhausted; switching to configured OpenAI backup for text")
                    try:
                        return openai_fallback.generate_text(
                            prompt, system_instruction=system_instruction
                        )
                    except openai_fallback.OpenAIFallbackError:
                        raise GeminiAPIError(
                            "Both Gemini and OpenAI backup are unavailable.",
                            diagnostic=exc.diagnostic,
                        ) from None
            raise
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            diagnostic = GeminiDiagnostic("STRUCTURED_OUTPUT_ERROR")
            self._log_failure(diagnostic, "generation")
            raise GeminiResponseError(
                "Gemini returned an empty text response.", diagnostic=diagnostic
            )
        return text.strip()

    def generate_structured(
        self,
        prompt: str,
        response_schema: type[StructuredResponse],
        *,
        system_instruction: str | None = None,
        before_attempt: Callable[[], None] | None = None,
    ) -> StructuredResponse:
        operation = {
            "SourceAnalysis": "source analysis",
            "OpportunityAnalysis": "opportunity analysis",
            "CompanyMatch": "matching",
            "TriageResult": "triage",
            "RequirementExtraction": "requirement extraction",
            "ReadinessAnalysis": "readiness",
            "ConnectionCheck": "connection diagnostic",
        }.get(response_schema.__name__, "structured generation")
        try:
            response = self._generate(
                prompt,
                operation=operation,
                before_attempt=before_attempt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=sanitize_gemini_schema(
                        response_schema.model_json_schema()
                    ),
                ),
            )
        except GeminiAPIError as exc:
            if exc.diagnostic is not None and exc.diagnostic.category == "QUOTA_EXCEEDED":
                from app.intelligence import openai_fallback

                if openai_fallback.fallback_enabled():
                    logger.warning(
                        "Gemini quota exhausted; switching to configured OpenAI backup for %s",
                        self._stage(operation),
                    )
                    try:
                        return openai_fallback.generate_structured(
                            prompt,
                            response_schema,
                            system_instruction=system_instruction,
                            before_attempt=before_attempt,
                        )
                    except openai_fallback.OpenAIFallbackError:
                        raise GeminiAPIError(
                            "Both Gemini and OpenAI backup are unavailable.",
                            diagnostic=exc.diagnostic,
                        ) from None
            raise

        try:
            parsed = getattr(response, "parsed", None)
            if parsed is not None:
                return response_schema.model_validate(parsed)

            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise GeminiResponseError(
                    "Gemini returned an empty structured response."
                )

            return response_schema.model_validate_json(text)
        except (GeminiResponseError, TypeError, ValueError, ValidationError) as exc:
            from app.intelligence.downstream_diagnostics import validation_details

            details = validation_details(exc, response_schema)
            diagnostic = GeminiDiagnostic("STRUCTURED_OUTPUT_ERROR")
            self._log_failure(diagnostic, operation)
            for detail in details:
                logger.warning(
                    "stage=%s schema=%s field=%s error=%s reason=%s",
                    self._stage(operation),
                    response_schema.__name__,
                    detail["field"],
                    detail["error"],
                    detail["reason"],
                )
            raise GeminiResponseError(
                "Gemini returned an invalid structured response.",
                diagnostic=diagnostic,
                validation_details=details,
            ) from None
