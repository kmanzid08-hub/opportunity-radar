"""Opt-in, budget-aware GroqCloud / NVIDIA NIM API backup.

Both endpoints use OpenAI-compatible chat completions and the application's
unchanged Pydantic schemas. No free API is silently enabled: account owners
must supply a separate key and enable each provider. No secret/prompt is logged.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import ceil
from threading import Lock
import json
import logging
import os
import time
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)

# Conservative defaults below the published Groq Free caps (30 RPM,
# 8k TPM, 1k RPD, 200k TPD for GPT-OSS 120B as of Nov 2026).
# NVIDIA values are SAFETY SETTINGS, not a claim about an account's quota.
PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "rpm": 25, "tpm": 7000, "rpd": 900, "tpd": 180000,
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "rpm": 32, "tpm": 40000, "rpd": 3000, "tpd": 3000000,
    },
}


class FreeAPIError(RuntimeError):
    """Safe error indicating no usable, schema-valid free backup response."""


def _enabled(prefix: str, environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return (
        source.get(f"{prefix.upper()}_FALLBACK_ENABLED", "").strip().lower()
        in {"true", "1", "yes", "on"}
        and bool(source.get(f"{prefix.upper()}_API_KEY", "").strip())
    )


def any_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return any(_enabled(provider, environ) for provider in PROVIDERS)


def _setting(source: Mapping[str, str], name: str, fallback: int) -> int:
    try:
        value = int(source.get(name, "") or fallback)
    except (ValueError, TypeError):
        return fallback
    return min(10000000, max(1, value))


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    base_url: str
    api_key: str
    rpm: int
    tpm: int
    rpd: int
    tpd: int


def enabled_configs(environ: Mapping[str, str] | None = None) -> list[ProviderConfig]:
    source = os.environ if environ is None else environ
    configs = []
    for name, default in PROVIDERS.items():
        if not _enabled(name, source):
            continue
        prefix = name.upper()
        configs.append(ProviderConfig(
            name=name,
            model=source.get(f"{prefix}_FALLBACK_MODEL", "").strip() or str(default["model"]),
            base_url=str(default["base_url"]),  # fixed allowlist, never user-provided
            api_key=source[f"{prefix}_API_KEY"].strip(),
            rpm=_setting(source, f"{prefix}_FALLBACK_RPM", int(default["rpm"])),
            tpm=_setting(source, f"{prefix}_FALLBACK_TPM", int(default["tpm"])),
            rpd=_setting(source, f"{prefix}_FALLBACK_RPD", int(default["rpd"])),
            tpd=_setting(source, f"{prefix}_FALLBACK_TPD", int(default["tpd"])),
        ))
    return configs


class RequestBudget:
    """Fail closed instead of making API requests that exceed a free allowance.

    Reservations are conservative: input and maximum output tokens are counted
    up front. Real provider usage is logged separately after success.
    """

    def __init__(self, config: ProviderConfig, clock: Callable[[], float] | None = None):
        self.config = config
        self.clock = clock or time.monotonic
        self._lock = Lock()
        self._last_minute: deque[tuple[float, int]] = deque()
        self._last_day: deque[tuple[float, int]] = deque()
        self._disabled_until = 0.0

    def reserve(self, estimated_tokens: int) -> bool:
        with self._lock:
            now = self.clock()
            if now < self._disabled_until:
                return False
            while self._last_minute and now - self._last_minute[0][0] >= 60:
                self._last_minute.popleft()
            while self._last_day and now - self._last_day[0][0] >= 86400:
                self._last_day.popleft()
            if (
                estimated_tokens > self.config.tpm
                or estimated_tokens > self.config.tpd
                or len(self._last_minute) >= self.config.rpm
                or len(self._last_day) >= self.config.rpd
                or sum(t for _, t in self._last_minute) + estimated_tokens > self.config.tpm
                or sum(t for _, t in self._last_day) + estimated_tokens > self.config.tpd
            ):
                return False
            item = (now, estimated_tokens)
            self._last_minute.append(item)
            self._last_day.append(item)
            return True

    def pause(self, seconds: float) -> None:
        with self._lock:
            self._disabled_until = max(self._disabled_until, self.clock() + seconds)


_budgets: dict[str, RequestBudget] = {}
_budgets_lock = Lock()


def _get_budget(config: ProviderConfig) -> RequestBudget:
    # Config may change in tests / reconfiguration without leaking old counters.
    key = config.name
    with _budgets_lock:
        budget = _budgets.get(key)
        if budget is None or budget.config != config:
            budget = RequestBudget(config)
            _budgets[key] = budget
        return budget


def _reserve_usage(prompt: str, system: str, schema_text: str, max_tokens: int) -> int:
    # Intentionally cautious: ~3 characters/token, including instructions,
    # structured response schema and all available completion tokens.
    return ceil((len(prompt) + len(system) + len(schema_text)) / 3) + max_tokens


def _schema_response_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "strict": False,
            "schema": schema.model_json_schema(),
        },
    }


def _attempt(
    config: ProviderConfig,
    *,
    prompt: str,
    system_instruction: str,
    response_schema: type[ResponseModel] | None,
    before_attempt: Callable[[], None] | None,
) -> ResponseModel | str:
    schema_text = (
        json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
        if response_schema is not None else ""
    )
    completion_allowance = 2200 if response_schema is not None else 1200
    estimated_tokens = _reserve_usage(
        prompt, system_instruction, schema_text, completion_allowance
    )
    budget = _get_budget(config)
    if not budget.reserve(estimated_tokens):
        raise FreeAPIError(
            f"{config.name} budget unavailable for this request or document"
        )

    # Shared user/workspace interactive AI limits still apply, and are charged
    # only for calls actually sent to the external API.
    from app.beta_security import reserve_ai_attempt
    reserve_ai_attempt()
    if before_attempt is not None:
        before_attempt()

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=75.0,
            max_retries=0,
        )
        args: dict[str, Any] = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": completion_allowance,
            "stream": False,
        }
        if response_schema is not None:
            args["response_format"] = _schema_response_format(response_schema)
        response = client.chat.completions.create(**args)
        if not response.choices or response.choices[0].finish_reason == "length":
            raise FreeAPIError("Truncated or missing provider response.")
        body = response.choices[0].message.content
        if not isinstance(body, str) or not body.strip():
            raise FreeAPIError("Empty provider response.")
        result: ResponseModel | str = (
            response_schema.model_validate_json(body)
            if response_schema is not None else body.strip()
        )
        usage = getattr(response, "usage", None)
        logger.info(
            "Community API usage provider=%s stage=%s input_tokens=%s "
            "output_tokens=%s total_tokens=%s",
            config.name,
            response_schema.__name__ if response_schema is not None else "TEXT",
            getattr(usage, "prompt_tokens", "unknown"),
            getattr(usage, "completion_tokens", "unknown"),
            getattr(usage, "total_tokens", "unknown"),
        )
        return result
    except FreeAPIError:
        raise
    except Exception as exc:
        kind = type(exc).__name__
        status = getattr(exc, "status_code", None)
        # Back off rate-limited / unavailable free APIs, without repeated calls.
        if status == 429 or kind == "RateLimitError":
            budget.pause(900)
        elif status in {401, 403}:
            budget.pause(3600)
        elif status in {502, 503, 504}:
            budget.pause(300)
        logger.warning(
            "Community API unavailable provider=%s error_type=%s status=%s",
            config.name, kind, status if isinstance(status, int) else "unknown",
        )
        raise FreeAPIError(f"{config.name} did not produce a valid response") from None


def generate_structured(
    prompt: str, response_schema: type[ResponseModel], *,
    system_instruction: str | None = None,
    before_attempt: Callable[[], None] | None = None,
) -> ResponseModel:
    if not prompt.strip():
        raise ValueError("Fallback prompt must not be blank.")
    for config in enabled_configs():
        try:
            result = _attempt(
                config, prompt=prompt, response_schema=response_schema,
                system_instruction=system_instruction or "Extract only supported facts.",
                before_attempt=before_attempt,
            )
            if isinstance(result, response_schema):
                return result
        except FreeAPIError:
            continue
    raise FreeAPIError("No enabled community API could produce a validated result.")


def generate_text(prompt: str, *, system_instruction: str | None = None) -> str:
    if not prompt.strip():
        raise ValueError("Fallback prompt must not be blank.")
    for config in enabled_configs():
        try:
            result = _attempt(
                config, prompt=prompt, response_schema=None,
                system_instruction=system_instruction or "Answer using supplied evidence.",
                before_attempt=None,
            )
            if isinstance(result, str):
                return result
        except FreeAPIError:
            continue
    raise FreeAPIError("No enabled community API could generate text.")
