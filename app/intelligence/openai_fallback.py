"""Opt-in OpenAI API backup for verified Gemini quota exhaustion.

A ChatGPT subscription is NOT an API credential. This module does nothing
unless OPENAI_FALLBACK_ENABLED=true and OPENAI_API_KEY is configured. No
automatic paid usage, credentials or company data are logged.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import os
from typing import TypeVar

from pydantic import BaseModel

from app.intelligence.gemini_diagnostics import safe_model_name

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5-mini"
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class OpenAIFallbackError(RuntimeError):
    """OpenAI backup could not supply a usable result."""


def fallback_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    opted_in = source.get("OPENAI_FALLBACK_ENABLED", "").strip().lower()
    return (
        opted_in in {"true", "1", "yes", "on"}
        and bool(source.get("OPENAI_API_KEY", "").strip())
    )


def _config() -> tuple[str, str]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_FALLBACK_MODEL", "").strip() or DEFAULT_MODEL
    if not fallback_enabled() or not key:
        raise OpenAIFallbackError("OpenAI API fallback is not enabled.")
    return key, model


def _before_request(before_attempt: Callable[[], None] | None = None) -> None:
    # Preserve the same per-workspace request allowance used by Gemini.
    from app.beta_security import reserve_ai_attempt

    reserve_ai_attempt()
    if before_attempt is not None:
        before_attempt()


def generate_structured(
    prompt: str,
    response_schema: type[ResponseModel],
    *,
    system_instruction: str | None = None,
    before_attempt: Callable[[], None] | None = None,
) -> ResponseModel:
    """Use one OpenAI request, validate using the existing Pydantic contract."""
    if not prompt.strip():
        raise ValueError("OpenAI fallback prompt must not be blank.")
    api_key, model = _config()
    _before_request(before_attempt)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=75.0, max_retries=0)
        response = client.responses.parse(
            model=model,
            instructions=system_instruction or "Extract only supported facts.",
            input=prompt,
            text_format=response_schema,
            store=False,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise OpenAIFallbackError(
                "OpenAI returned no valid structured output."
            )
        result = response_schema.model_validate(parsed)
    except Exception as exc:
        logger.warning(
            "OpenAI fallback structured output failed model=%s error_type=%s",
            safe_model_name(model, api_key), type(exc).__name__,
        )
        raise OpenAIFallbackError(
            "OpenAI backup could not produce valid structured output."
        ) from None
    logger.info(
        "OpenAI fallback structured output succeeded model=%s schema=%s",
        safe_model_name(model, api_key), response_schema.__name__,
    )
    return result


def generate_text(
    prompt: str,
    *,
    system_instruction: str | None = None,
) -> str:
    """Use the same opt-in backup for Gemini text-mode requests."""
    if not prompt.strip():
        raise ValueError("OpenAI fallback prompt must not be blank.")
    api_key, model = _config()
    _before_request()
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=75.0, max_retries=0)
        response = client.responses.create(
            model=model,
            instructions=system_instruction or "Answer using supplied evidence.",
            input=prompt,
            store=False,
        )
        output = response.output_text
        if not isinstance(output, str) or not output.strip():
            raise OpenAIFallbackError("OpenAI returned empty text.")
    except Exception as exc:
        logger.warning(
            "OpenAI fallback text failed model=%s error_type=%s",
            safe_model_name(model, api_key), type(exc).__name__,
        )
        raise OpenAIFallbackError("OpenAI backup could not generate text.") from None
    logger.info(
        "OpenAI fallback text succeeded model=%s",
        safe_model_name(model, api_key),
    )
    return output.strip()
