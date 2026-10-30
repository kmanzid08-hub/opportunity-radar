"""SOURCE-only safe request metadata and HTTP 400 diagnosis. No provider dumps."""

import json
from copy import deepcopy
from typing import Any

from google.genai import _transformers, errors, types

from app.intelligence.gemini_diagnostics import classify_gemini_error, safe_model_name
from app.intelligence.schemas import SourceAnalysis
from app.intelligence.gemini_schema import sanitize_gemini_schema


def source_request_metadata(
    *,
    model: str,
    api_key: str | None,
    prompt: str,
    config: types.GenerateContentConfig,
    content_chars: int,
) -> dict[str, Any]:
    if config.response_schema != sanitize_gemini_schema(
        SourceAnalysis.model_json_schema()
    ):
        raise ValueError("SOURCE diagnostic requires the real SourceAnalysis schema")
    # Same transformation used by the installed SDK; this performs no I/O.
    schema = _transformers.t_schema(None, deepcopy(config.response_schema))
    if schema is None:
        raise ValueError("SOURCE schema is missing")
    fields = {
        name: {
            "type": field.type.value if field.type else None,
            "nullable": bool(field.nullable),
            "enum_count": len(field.enum or []),
            "required": name in (schema.required or []),
        }
        for name, field in (schema.properties or {}).items()
    }
    return {
        "model": safe_model_name(model, api_key),
        "request_mode": "generateContent/response_schema",
        "response_mime_type": config.response_mime_type,
        "temperature": config.temperature,
        "max_output_tokens": config.max_output_tokens,
        "top_p": config.top_p,
        "top_k": config.top_k,
        "candidate_count": config.candidate_count,
        "system_instruction_chars": (
            len(config.system_instruction)
            if isinstance(config.system_instruction, str)
            else 0
        ),
        "prompt_chars": len(prompt),
        "content_chars": content_chars,
        "schema_bytes": len(schema.model_dump_json(exclude_none=True).encode()),
        "schema_keywords": sorted(schema.model_dump(exclude_none=True)),
        "fields": fields,
    }


def source_failure_details(exc: Exception) -> dict[str, Any]:
    diagnostic = classify_gemini_error(exc)
    if not isinstance(exc, errors.APIError) or exc.code != 400:
        return {
            "category": diagnostic.category,
            "http_status": diagnostic.http_status,
            "provider_message": "Provider text withheld.",
        }
    message = exc.message if isinstance(exc.message, str) else ""
    details = exc.details if isinstance(exc.details, dict) else {}
    # Inspect internally, but output only fixed vocabulary. Never echo arbitrary
    # provider messages, BadRequest descriptions, header values, or request IDs.
    evidence = (message + " " + json.dumps(details, default=lambda _: "")).lower()
    if any(
        term in evidence
        for term in (
            "context length",
            "token limit",
            "too many tokens",
            "request too large",
            "payload too large",
        )
    ):
        category = "CONTEXT_TOO_LARGE"
    elif any(
        term in evidence
        for term in (
            "response_schema",
            "responseschema",
            "response_json_schema",
            "responsejsonschema",
            "json schema",
            "additionalproperties",
            "anyof",
            "nullable",
            "enum",
        )
    ):
        category = "INVALID_SCHEMA"
    elif "model" in evidence and any(
        term in evidence
        for term in ("not supported", "unsupported", "not available", "not found")
    ):
        category = "MODEL_PARAMETER_ERROR"
    elif any(
        term in evidence
        for term in (
            "unknown name",
            "unknown field",
            "unsupported parameter",
            "unrecognized parameter",
        )
    ):
        category = "UNSUPPORTED_PARAMETER"
    elif any(
        term in evidence
        for term in (
            "generation_config",
            "generationconfig",
            "temperature",
            "max_output_tokens",
            "maxoutputtokens",
            "top_p",
            "topp",
            "top_k",
            "topk",
        )
    ):
        category = "INVALID_GENERATION_CONFIG"
    elif any(
        term in evidence
        for term in (
            "contents",
            "content",
            "parts",
            "system_instruction",
            "systeminstruction",
        )
    ):
        category = "INVALID_CONTENT"
    else:
        category = "OTHER_INVALID_REQUEST"

    # Safe excerpts: only pre-approved, literal phrases and schema/config names.
    phrases = [
        phrase
        for phrase in (
            "Invalid JSON payload received",
            "Unknown name",
            "Unknown field",
            "Cannot find field",
            "Unsupported parameter",
            "Invalid value",
            "not supported",
            "must be specified",
            "exceeds the maximum",
        )
        if phrase.lower() in evidence
    ]
    fields = [
        field
        for field in (
            "additionalProperties",
            "additional_properties",
            "anyOf",
            "nullable",
            "enum",
            "minimum",
            "maximum",
            "propertyOrdering",
            "$defs",
            "$ref",
            "response_schema",
            "responseSchema",
            "response_json_schema",
            "responseJsonSchema",
            "generation_config",
            "generationConfig",
            "temperature",
            "max_output_tokens",
            "maxOutputTokens",
            "top_p",
            "topP",
            "top_k",
            "topK",
            "system_instruction",
            "systemInstruction",
            "contents",
            "parts",
        )
        if field.lower() in evidence
    ]
    return {
        "category": category,
        "http_status": 400,
        "provider_message": "; ".join(phrases) or "Provider text withheld.",
        "provider_fields": fields,
    }
