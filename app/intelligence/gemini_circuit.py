"""Process-wide, bounded Gemini circuit breaker for opt-in AI backup.

This never schedules paid API usage itself. Only callers that have already
explicitly configured OpenAI fallback may use it. A new process starts closed.
"""
from __future__ import annotations

from collections.abc import Callable
import logging
from threading import Lock
import time

from app.intelligence.gemini_diagnostics import GeminiDiagnostic

logger = logging.getLogger(__name__)

BACKUP_CATEGORIES = frozenset({
    "QUOTA_EXCEEDED", "MODEL_UNAVAILABLE",
})

# Check provider availability periodically, without hammering a broken API.
COOLDOWN_SECONDS = {
    "QUOTA_EXCEEDED": 3600,
    "MODEL_UNAVAILABLE": 300,
}


class GeminiProviderCircuit:
    """Shared between clients in a single scan process; thread-safe."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._lock = Lock()
        self._blocked_until = 0.0
        self._diagnostic: GeminiDiagnostic | None = None

    def unavailable(self) -> GeminiDiagnostic | None:
        with self._lock:
            if self._diagnostic is None:
                return None
            if self._clock() >= self._blocked_until:
                self._diagnostic = None
                self._blocked_until = 0.0
                return None
            return self._diagnostic

    def record_failure(self, diagnostic: GeminiDiagnostic) -> None:
        duration = COOLDOWN_SECONDS.get(diagnostic.category)
        if duration is None:
            return
        with self._lock:
            was_open = self._diagnostic is not None and (
                self._clock() < self._blocked_until
            )
            self._diagnostic = diagnostic
            self._blocked_until = self._clock() + duration
        if not was_open:
            logger.warning(
                "Gemini temporarily bypassed category=%s http_status=%s "
                "cooldown_seconds=%s; using enabled OpenAI backup",
                diagnostic.category,
                diagnostic.http_status if diagnostic.http_status is not None else "unknown",
                duration,
            )

    def record_success(self) -> None:
        with self._lock:
            self._blocked_until = 0.0
            self._diagnostic = None


PROVIDER_CIRCUIT = GeminiProviderCircuit()
