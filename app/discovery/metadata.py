from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryMetadata:
    categories: tuple[str, ...] = ()
    queries: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


def decode_discovery_metadata(
    value: str | None,
) -> DiscoveryMetadata:
    if not value or not value.strip():
        return DiscoveryMetadata()

    cleaned = value.strip()

    try:
        payload = json.loads(cleaned)
    except (TypeError, ValueError):
        return DiscoveryMetadata(queries=(cleaned,))

    if not isinstance(payload, dict):
        return DiscoveryMetadata(queries=(cleaned,))

    return DiscoveryMetadata(
        categories=_clean_values(payload.get("categories")),
        queries=_clean_values(payload.get("queries")),
        tags=_clean_values(payload.get("tags")),
    )


def merge_discovery_metadata(
    existing: str | None,
    *,
    categories: Iterable[str] = (),
    queries: Iterable[str] = (),
    tags: Iterable[str] = (),
) -> str:
    current = decode_discovery_metadata(existing)
    payload = {
        "categories": sorted(
            set(current.categories).union(_clean_values(categories))
        ),
        "queries": sorted(
            set(current.queries).union(_clean_values(queries))
        ),
        "tags": sorted(
            set(current.tags).union(_clean_values(tags))
        ),
    }

    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _clean_values(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)

    if not isinstance(values, Iterable):
        return ()

    cleaned = {
        str(value).strip()
        for value in values
        if str(value).strip()
    }

    return tuple(sorted(cleaned))
