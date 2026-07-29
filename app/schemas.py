from dataclasses import dataclass
from datetime import date


@dataclass
class RawOpportunity:
    organisation_name: str | None
    title: str
    source_name: str
    source_url: str
    description: str | None = None
    deadline: date | None = None


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