"""Validated company intelligence shared by persistence, forms, and matching."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanyIntelligence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilities: list[str] = Field(default_factory=list)
    areas_of_expertise: list[str] = Field(default_factory=list)
    assignment_types: list[str] = Field(default_factory=list)
    client_types: list[str] = Field(default_factory=list)
    sector_experience: list[str] = Field(default_factory=list)
    relevant_experience: list[str] = Field(default_factory=list)
    track_record: list[str] = Field(default_factory=list)
    preferred_sectors: list[str] = Field(default_factory=list)
    excluded_services: list[str] = Field(default_factory=list)
    excluded_sectors: list[str] = Field(default_factory=list)
    excluded_opportunity_types: list[str] = Field(default_factory=list)
    excluded_countries: list[str] = Field(default_factory=list)

    @field_validator("*")
    @classmethod
    def normalize_entries(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            entry = " ".join(value.split())
            if entry and entry.casefold() not in seen:
                cleaned.append(entry)
                seen.add(entry.casefold())
        return cleaned


# Form values are one entry per line so assignment descriptions can contain commas.
INTELLIGENCE_SECTIONS = (
    ("Capabilities", (
        ("capabilities", "Capabilities"),
        ("areas_of_expertise", "Areas of expertise"),
    )),
    ("Experience", (
        ("assignment_types", "Assignment types"),
        ("client_types", "Client types"),
        ("sector_experience", "Sector experience"),
        ("relevant_experience", "Relevant experience"),
        ("track_record", "Track record and evidence"),
    )),
    ("Exclusions", (
        ("excluded_services", "Excluded services"),
        ("excluded_sectors", "Excluded sectors"),
        ("excluded_opportunity_types", "Excluded opportunity types"),
        ("excluded_countries", "Excluded countries"),
    )),
)
