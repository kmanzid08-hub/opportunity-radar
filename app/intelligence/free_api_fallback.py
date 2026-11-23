"""Opt-in, budget-aware GroqCloud / NVIDIA NIM API backup.

Both endpoints use OpenAI-compatible chat completions and the application's
unchanged Pydantic schemas. No free API is silently enabled: account owners
must supply a separate key and enable each provider. No secret/prompt is logged.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from threading import Lock
import json
import logging
import os
import time
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import select, text

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
        "model": "meta/muse-glimmer-30b",
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
        model = source.get(f"{prefix}_FALLBACK_MODEL", "").strip() or str(default["model"])
        # The old NVIDIA hosted model now returns HTTP 410. A GitHub Actions
        # variable may still contain this old value; never keep selecting it.
        if name == "nvidia" and model == "openai/gpt-oss-20b":
            logger.warning("Deprecated NVIDIA model override ignored; using supported default")
            model = str(default["model"])
        configs.append(ProviderConfig(
            name=name,
            model=model,
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
        self._timeout_streak = 0

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

    def record_timeout(self) -> bool:
        """Open a temporary circuit after repeated network timeouts."""
        with self._lock:
            self._timeout_streak += 1
            if self._timeout_streak >= 2:
                self._disabled_until = max(self._disabled_until, self.clock() + 900)
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._timeout_streak = 0


_budgets: dict[str, RequestBudget] = {}
_budgets_lock = Lock()
_route_lock = Lock()
_route_sequence = 0


def _ordered_configs(configs: list[ProviderConfig]) -> list[ProviderConfig]:
    """Rotate the first-choice provider on each request across both quotas."""
    global _route_sequence
    if len(configs) < 2:
        return configs
    with _route_lock:
        start = _route_sequence % len(configs)
        _route_sequence += 1
    return configs[start:] + configs[:start]


def _shared_budget_enabled() -> bool:
    """PostgreSQL shared counters survive GitHub Actions process restarts."""
    value = os.getenv("AI_SHARED_PROVIDER_BUDGETS", "auto").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return False
    if value in {"1", "true", "yes", "on"}:
        return True
    from app.database import DATABASE_URL
    return DATABASE_URL.startswith(("postgresql", "postgres:"))


def _outage_key(config: ProviderConfig) -> str:
    # Key by provider AND model: replacing a retired model must recover
    # immediately without waiting for an older model's circuit to expire.
    return f"ai-provider-outage:{config.name}:{config.model}"


def _record_permanent_model_failure(config: ProviderConfig, seconds: int) -> None:
    """Persist unsupported-model failures across hourly GitHub Actions jobs."""
    if not _shared_budget_enabled():
        return
    from app.beta_models import BetaUsage
    from app.database import SessionLocal

    try:
        with SessionLocal() as db:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
                    {"lock_name": _outage_key(config)},
                )
            row = db.get(BetaUsage, _outage_key(config))
            expiry = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            if row is None:
                db.add(BetaUsage(key=_outage_key(config), count=1, expires_at=expiry))
            else:
                row.count += 1
                row.expires_at = expiry
            db.commit()
    except Exception as exc:
        # Local circuit still blocks retries even if shared DB is unavailable.
        logger.warning(
            "Could not persist retired model quarantine provider=%s error_type=%s",
            config.name, type(exc).__name__,
        )


def _reserve_shared_budget(config: ProviderConfig, estimate: int) -> bool:
    """Atomically enforce minute AND daily limits across app + GitHub jobs.

    Reuse the existing beta_usage quota table. PostgreSQL advisory lock protects
    all four counters for this provider within one short DB transaction.
    No schema migration, no secret or prompt persistence.
    """
    if not _shared_budget_enabled():
        return True
    from app.beta_models import BetaUsage
    from app.database import SessionLocal

    now = datetime.now(timezone.utc)
    minute_start = now.replace(second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    minute = minute_start.strftime("%Y%m%d%H%M")
    day = day_start.strftime("%Y%m%d")
    prefix = f"ai-provider-budget:{config.name}:"
    requirements = [
        (f"{prefix}m:{minute}:r", 1, config.rpm, minute_start + timedelta(minutes=3)),
        (f"{prefix}m:{minute}:t", estimate, config.tpm, minute_start + timedelta(minutes=3)),
        (f"{prefix}d:{day}:r", 1, config.rpd, day_start + timedelta(days=3)),
        (f"{prefix}d:{day}:t", estimate, config.tpd, day_start + timedelta(days=3)),
    ]
    keys = [item[0] for item in requirements]
    try:
        with SessionLocal() as db:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
                    {"lock_name": prefix},
                )
            outage = db.get(BetaUsage, _outage_key(config))
            if outage is not None:
                expiry = outage.expires_at
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry > now:
                    return False
            present = {
                row.key: row
                for row in db.scalars(select(BetaUsage).where(BetaUsage.key.in_(keys)))
            }
            if any(
                (present[key].count if key in present else 0) + amount > ceiling
                for key, amount, ceiling, _ in requirements
            ):
                return False
            for key, amount, _ceiling, expiry in requirements:
                row = present.get(key)
                if row is None:
                    db.add(BetaUsage(key=key, count=amount, expires_at=expiry))
                else:
                    row.count += amount
            db.commit()
        return True
    except Exception as exc:
        # Do not make an unmetered free-tier call when central quota storage
        # is unavailable. The caller may use a different verified provider.
        logger.warning(
            "Shared AI provider budget unavailable provider=%s type=%s",
            config.name, type(exc).__name__,
        )
        return False


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
    if not budget.reserve(estimated_tokens) or not _reserve_shared_budget(
        config, estimated_tokens
    ):
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
            timeout=float(_setting(os.environ, f"{config.name.upper()}_TIMEOUT_SECONDS", 22 if config.name == "nvidia" else 45)),
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
            if config.name == "groq":
                # The supported GPT-OSS 120B model offers best-effort JSON
                # schema mode. Strict mode is unsuitable for optional fields.
                args["response_format"] = _schema_response_format(response_schema)
            else:
                # Muse Glimmer offers a live prototype endpoint but does not
                # advertise native JSON schema mode. Demand a JSON object
                # and validate it against our unchanged Pydantic model.
                args["messages"][0]["content"] += (
                    "\nReturn ONLY a valid JSON object, without code fences, "
                    "analysis, or commentary, following this JSON schema:\n"
                    + schema_text
                )
                if config.model == "meta/muse-glimmer-30b":
                    # NVIDIA exposes 'none' to avoid consuming the short
                    # completion budget on internal reasoning.
                    args["reasoning_effort"] = "none"
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
        budget.record_success()
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
        elif status in {404, 410}:
            # Unsupported/retired model: retrying every notice and every
            # hourly job cannot help. Quarantine this exact model for 24 h.
            budget.pause(86_400)
            _record_permanent_model_failure(config, 86_400)
        elif status in {502, 503, 504}:
            budget.pause(300)
        elif "Timeout" in kind or isinstance(exc, TimeoutError):
            if budget.record_timeout():
                _record_permanent_model_failure(config, 900)
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
    # Public-source classification is the default allowed use. CompanyMatch,
    # TriageResult, and readiness prompts include proprietary workspace
    # capabilities, personnel or bid information; never send those to free
    # evaluation services without a second explicit privacy decision.
    public_schemas = {"SourceAnalysis", "OpportunityAnalysis"}
    if response_schema.__name__ not in public_schemas and (
        os.getenv("COMMUNITY_API_ALLOW_COMPANY_DATA", "").strip().lower()
        not in {"true", "1", "yes", "on"}
    ):
        raise FreeAPIError(
            "Free evaluation APIs are disabled for company-private workflows."
        )
    for config in _ordered_configs(enabled_configs()):
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
    # Generic text requests have no schema identifying whether their
    # content is public. Fail closed to protect company and user data.
    if os.getenv("COMMUNITY_API_ALLOW_COMPANY_DATA", "").strip().lower() not in {
        "true", "1", "yes", "on",
    }:
        raise FreeAPIError(
            "Free evaluation APIs are disabled for unspecified/private text."
        )
    for config in _ordered_configs(enabled_configs()):
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
