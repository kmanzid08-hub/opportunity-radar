from __future__ import annotations

from urllib.parse import (
    parse_qsl,
    urlencode,
    urlparse,
    urlunparse,
)

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Source


TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
}


def normalise_url(url: str) -> str:
    """
    Normalise URLs so tracking parameters do not create duplicates.
    """
    parsed = urlparse(url.strip())

    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()

    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path or "/"

    if path != "/":
        path = path.rstrip("/")

    query_items = [
        (key, value)
        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
        if key.lower() not in TRACKING_PARAMETERS
    ]

    query = urlencode(sorted(query_items))

    return urlunparse(
        (
            scheme,
            netloc,
            path,
            "",
            query,
            "",
        )
    )


def normalise_domain(url: str) -> str:
    parsed = urlparse(normalise_url(url))

    return parsed.netloc.lower()


def get_base_url(url: str) -> str:
    parsed = urlparse(normalise_url(url))

    return f"{parsed.scheme}://{parsed.netloc}"


def save_discovered_source(
    monitor_url: str,
    organisation_name: str | None,
    source_type: str,
    discovered_from: str,
    discovery_query: str,
    confidence_score: float,
) -> bool:
    """
    Save a discovered source or update its confidence when it already exists.

    Returns:
        True when a new record is inserted.
        False when the source already exists or the URL is invalid.
    """
    normalised_monitor_url = normalise_url(monitor_url)
    domain = normalise_domain(normalised_monitor_url)

    if not domain:
        return False

    with SessionLocal() as db:
        existing = db.scalar(
            select(Source).where(
                Source.monitor_url == normalised_monitor_url
            )
        )

        if existing:
            changed = False

            if confidence_score > existing.confidence_score:
                existing.confidence_score = confidence_score
                changed = True

            if (
                source_type
                and source_type != "Unknown"
                and existing.source_type == "Unknown"
            ):
                existing.source_type = source_type
                changed = True

            if (
                organisation_name
                and not existing.organisation_name
            ):
                existing.organisation_name = organisation_name
                changed = True

            if changed:
                db.commit()

            return False

        source = Source(
            organisation_name=organisation_name,
            base_url=get_base_url(normalised_monitor_url),
            monitor_url=normalised_monitor_url,
            domain=domain,
            source_type=source_type or "Unknown",
            discovered_from=discovered_from,
            discovery_query=discovery_query,
            confidence_score=confidence_score,
            is_active=True,
            is_approved=False,
        )

        db.add(source)
        db.commit()

        return True