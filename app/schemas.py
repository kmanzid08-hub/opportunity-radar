from dataclasses import dataclass, field
from datetime import date


@dataclass
class RawOpportunity:
    organisation_name: str | None
    title: str
    source_name: str
    source_url: str
    description: str | None = None
    deadline: date | None = None
    country_context: str | None = None
    outgoing_links: list[str] = field(default_factory=list)


@dataclass
class FilteredOpportunity:
    organisation_name: str | None
    title: str
    category: str
    source_name: str
    source_url: str
    match_score: int
    match_reason: str
    description: str | None = None
    deadline: date | None = None
