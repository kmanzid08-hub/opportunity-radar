"""Exact country-name normalization using tzdata's maintained ISO 3166 catalog.

No fuzzy matches, geocoding, city inference, or source text in diagnostics.
ISO countries/territories are recognized; regional prose is not a country.
"""

from functools import lru_cache
from importlib.resources import files
import logging
import unicodedata


logger = logging.getLogger(__name__)


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.replace("’", "'"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().replace("&", "and").split())


@lru_cache(maxsize=1)
def _country_names() -> dict[str, str]:
    catalog = files("tzdata.zoneinfo").joinpath("iso3166.tab").read_text("utf-8")
    names: dict[str, str] = {}
    # Stable display names and exact aliases, not country-specific business rules.
    aliases = {
        "CI": ("Côte d’Ivoire", "Ivory Coast", "Cote d'Ivoire"),
        "CD": (
            "Democratic Republic of the Congo",
            "DR Congo",
            "DRC",
            "Congo, Democratic Republic of the",
        ),
        "CG": ("Republic of the Congo", "Congo", "Congo Republic"),
        "TZ": (
            "Tanzania",
            "United Republic of Tanzania",
            "Tanzania, United Republic of",
        ),
        "US": ("United States", "United States of America", "USA"),
        "GB": ("United Kingdom", "UK"),
        "CZ": ("Czechia", "Czech Republic"),
        "TR": ("Türkiye", "Turkey"),
        "KR": ("South Korea", "Republic of Korea"),
        "KP": ("North Korea", "Democratic People's Republic of Korea"),
    }
    for line in catalog.splitlines():
        if not line or line.startswith("#"):
            continue
        code, name = line.split("\t", 1)
        alternatives = aliases.get(code, (name,))
        for variant in (name, *alternatives):
            names[_key(variant)] = alternatives[0]
    return names


def normalize_country(value: str | None) -> str | None:
    return _country_names().get(_key(value)) if value else None


def normalize_countries(values: list[str], *, field: str) -> list[str]:
    normalized = [normalize_country(value) for value in values]
    rejected = sum(value is None for value in normalized)
    if rejected:
        logger.warning("Invalid geography field=%s rejected_count=%d", field, rejected)
    return list(dict.fromkeys(value for value in normalized if value is not None))
