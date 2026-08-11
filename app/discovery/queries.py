from __future__ import annotations


DISCOVERY_CATEGORIES: dict[str, tuple[str, ...]] = {
    "general_procurement": (
        '"procurement opportunities" {country}',
        '"tender opportunities" {country}',
    ),

    "ict_software": (
        '"ICT services" tender {country}',
        '"software development" RFP {country}',
    ),

    "construction_infrastructure": (
        '"construction works" tender {country}',
        '"civil works" procurement {country}',
    ),

    "engineering": (
        '"engineering services" tender {country}',
        '"engineering consultancy" RFP {country}',
    ),

    "energy": (
        '"renewable energy" tender {country}',
        '"electrical works" procurement {country}',
    ),

    "healthcare": (
        '"medical equipment" tender {country}',
        '"health services" procurement {country}',
    ),

    "agriculture": (
        '"agricultural services" tender {country}',
        '"agricultural inputs" procurement {country}',
    ),

    "logistics_transport": (
        '"logistics services" tender {country}',
        '"transport services" procurement {country}',
    ),

    "equipment_supplies": (
        '"supply of equipment" tender {country}',
        '"supply of goods" procurement {country}',
    ),

    "professional_services": (
        '"consultancy services" tender {country}',
        '"professional services" RFP {country}',
    ),

    "finance_audit_tax": (
        '"audit services" tender {country}',
        '"financial services" RFP {country}',
    ),

    "training_education": (
        '"training services" tender {country}',
        '"capacity building" RFP {country}',
    ),

    "research_evaluation": (
        '"research services" tender {country}',
        '"monitoring and evaluation" RFP {country}',
    ),

    "marketing_communications": (
        '"communications services" tender {country}',
        '"marketing services" RFP {country}',
    ),

    "environmental": (
        '"environmental services" tender {country}',
        '"environmental impact assessment" RFP {country}',
    ),

    "security_facilities": (
        '"security services" tender {country}',
        '"facility management" procurement {country}',
    ),
}


def build_discovery_queries(
    country: str,
) -> list[str]:
    """
    Build broad opportunity-source discovery queries
    for a target country.

    Country is runtime data rather than an architectural
    assumption of the discovery engine.
    """

    clean_country = country.strip()

    if not clean_country:
        raise ValueError(
            "country is required"
        )

    queries: list[str] = []

    for query_templates in DISCOVERY_CATEGORIES.values():
        for query_template in query_templates:
            queries.append(
                query_template.format(
                    country=clean_country,
                )
            )

    return queries


def build_category_queries(
    country: str,
) -> dict[str, list[str]]:
    """
    Return queries grouped by discovery category.

    This is useful for discovery metrics and logging.
    """

    clean_country = country.strip()

    if not clean_country:
        raise ValueError(
            "country is required"
        )

    return {
        category: [
            template.format(
                country=clean_country,
            )
            for template in templates
        ]
        for category, templates
        in DISCOVERY_CATEGORIES.items()
    }


# Backward compatibility with the existing discovery runner.
#
# The application can later replace this default with the
# active organization's countries.
DISCOVERY_QUERIES: list[str] = (
    build_discovery_queries(
        "Rwanda"
    )
)
