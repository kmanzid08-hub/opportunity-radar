"""Content-free source fetch diagnostics; never serialize exceptions or URLs."""

from dataclasses import dataclass
import logging
import socket

import requests


logger = logging.getLogger(__name__)


class SourceFetchError(requests.RequestException):
    def __init__(self, category: str, status: int | None = None) -> None:
        super().__init__("Source fetch failed")
        self.category = category
        self.status = status


@dataclass(frozen=True)
class FetchFailure:
    category: str
    http_status: int | None = None


def classify_fetch_failure(error: Exception) -> FetchFailure:
    if isinstance(error, SourceFetchError):
        allowed = {
            "BLOCKED",
            "HTTP_ERROR",
            "EMPTY_CONTENT",
            "UNSUPPORTED_CONTENT",
            "LIMIT_EXCEEDED",
        }
        category = error.category if error.category in allowed else "FETCH_ERROR"
        return FetchFailure(category, error.status)
    if isinstance(error, (requests.Timeout, TimeoutError)):
        return FetchFailure("TIMEOUT")
    if isinstance(error, socket.gaierror):
        return FetchFailure("DNS_NETWORK_ERROR")
    if isinstance(error, requests.ConnectionError):
        return FetchFailure("DNS_NETWORK_ERROR")
    if isinstance(error, requests.RequestException) and error.response is not None:
        status = error.response.status_code
        return FetchFailure(
            "BLOCKED" if status in {401, 403, 429, 451} else "HTTP_ERROR", status
        )
    return FetchFailure("FETCH_ERROR")


def report_fetch_failure(error: Exception) -> str:
    failure = classify_fetch_failure(error)
    summary = f"category={failure.category} http_status={failure.http_status}"
    logger.warning("Source fetch failed %s", summary)
    return summary
