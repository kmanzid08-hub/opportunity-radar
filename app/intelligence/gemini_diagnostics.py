"""Allowlisted Gemini failure metadata; never serialize provider error payloads."""

from dataclasses import dataclass
import re
import socket
import ssl

from google.genai import errors
import httpx
from pydantic import ValidationError
import requests


RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def _temporary_network_error(exc: Exception) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ssl.SSLError, requests.exceptions.SSLError)):
            return False
        current = current.__cause__ or current.__context__
    if isinstance(exc, socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    return isinstance(
        exc,
        (
            ConnectionError,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            requests.ConnectionError,
        ),
    )


@dataclass(frozen=True, slots=True)
class GeminiDiagnostic:
    category: str
    http_status: int | None = None
    retryable: bool = False


def safe_model_name(model: str, api_key: str | None = None) -> str:
    if (
        (api_key and api_key in model)
        or "AIza" in model
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", model)
    ):
        return "[redacted]"
    return model


def classify_gemini_error(exc: Exception) -> GeminiDiagnostic:
    """Inspect typed SDK fields internally; return only fixed, public labels.

    A bare 429 is rate limiting. Only explicit quota evidence is classified as
    quota exhaustion; neither label authorizes automatic retries.
    """
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, requests.Timeout)):
        return GeminiDiagnostic("TIMEOUT", retryable=True)
    if isinstance(
        exc,
        (
            ConnectionError,
            socket.gaierror,
            httpx.TransportError,
            requests.ConnectionError,
        ),
    ):
        return GeminiDiagnostic(
            "NETWORK_ERROR", retryable=_temporary_network_error(exc)
        )
    if isinstance(exc, ValidationError):
        return GeminiDiagnostic("STRUCTURED_OUTPUT_ERROR")
    if not isinstance(exc, errors.APIError):
        return GeminiDiagnostic("UNKNOWN_PROVIDER_ERROR")

    code = exc.code
    status = code if isinstance(code, int) and 100 <= code <= 599 else None
    payload = exc.details if isinstance(exc.details, dict) else {}
    body = payload.get("error", payload)
    body = body if isinstance(body, dict) else {}
    details = body.get("details", [])
    details = details if isinstance(details, list) else []
    reasons = {
        item.get("reason")
        for item in details
        if isinstance(item, dict) and isinstance(item.get("reason"), str)
    }
    quota_detail = any(
        isinstance(item, dict)
        and item.get("@type") == "type.googleapis.com/google.rpc.QuotaFailure"
        for item in details
    )
    # Messages are used only for classification, never returned or logged.
    message = exc.message.lower() if isinstance(exc.message, str) else ""
    provider_status = exc.status
    if (
        status == 401
        or provider_status == "UNAUTHENTICATED"
        or reasons & {"API_KEY_INVALID", "API_KEY_EXPIRED", "CREDENTIALS_MISSING"}
        or (status in {400, 403} and "api key not valid" in message)
    ):
        category = "AUTHENTICATION_ERROR"
    elif status == 403 or provider_status == "PERMISSION_DENIED":
        category = "PERMISSION_ERROR"
    elif status == 429 or provider_status == "RESOURCE_EXHAUSTED":
        category = (
            "QUOTA_EXCEEDED"
            if quota_detail
            or reasons & {"QUOTA_EXCEEDED", "DAILY_LIMIT_EXCEEDED"}
            or "quota" in message
            else "RATE_LIMIT"
        )
    elif status == 404 or provider_status == "NOT_FOUND":
        category = "MODEL_NOT_FOUND"
    elif status == 503 or provider_status == "UNAVAILABLE":
        category = "MODEL_UNAVAILABLE"
    elif status in {408, 504} or provider_status == "DEADLINE_EXCEEDED":
        category = "TIMEOUT"
    elif status in {400, 405, 413, 415, 422} or provider_status == "INVALID_ARGUMENT":
        category = "INVALID_REQUEST"
    else:
        category = "UNKNOWN_PROVIDER_ERROR"
    retryable = status in RETRYABLE_HTTP_STATUSES
    return GeminiDiagnostic(category, status, retryable)
