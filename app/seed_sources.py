from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Source


RWANDA_SOURCES = (
    {
        "organisation_name": "Rwanda Development Board",
        "base_url": "https://rdb.rw",
        "monitor_url": "https://rdb.rw",
        "source_type": "Government Institution",
    },
    {
        "organisation_name": "Rwanda Revenue Authority",
        "base_url": "https://www.rra.gov.rw",
        "monitor_url": "https://www.rra.gov.rw",
        "source_type": "Government Institution",
    },
    {
        "organisation_name": "Rwanda Public Procurement Authority",
        "base_url": "https://www.rppa.gov.rw",
        "monitor_url": "https://www.rppa.gov.rw",
        "source_type": "Procurement Authority",
    },
    {
        "organisation_name": "University of Rwanda",
        "base_url": "https://ur.ac.rw",
        "monitor_url": "https://ur.ac.rw",
        "source_type": "University",
    },
    {
        "organisation_name": "Rwanda Utilities Regulatory Authority",
        "base_url": "https://www.rura.rw",
        "monitor_url": "https://www.rura.rw",
        "source_type": "Government Institution",
    },
    {
        "organisation_name": "Rwanda Social Security Board",
        "base_url": "https://www.rssb.rw",
        "monitor_url": "https://www.rssb.rw",
        "source_type": "Public Institution",
    },
    {
        "organisation_name": "National Bank of Rwanda",
        "base_url": "https://www.bnr.rw",
        "monitor_url": "https://www.bnr.rw",
        "source_type": "Public Institution",
    },
    {
        "organisation_name": "Rwanda Governance Board",
        "base_url": "https://www.rgb.rw",
        "monitor_url": "https://www.rgb.rw",
        "source_type": "Government Institution",
    },
    {
        "organisation_name": "Rwanda Environment Management Authority",
        "base_url": "https://www.rema.gov.rw",
        "monitor_url": "https://www.rema.gov.rw",
        "source_type": "Government Institution",
    },
    {
        "organisation_name": "Job in Rwanda",
        "base_url": "https://www.jobinrwanda.com",
        "monitor_url": "https://www.jobinrwanda.com",
        "source_type": "Job Portal",
    },
)


def normalise_domain(
    url: str,
) -> str:
    parsed = urlparse(
        url
    )

    domain = parsed.netloc.lower()

    if domain.startswith(
        "www."
    ):
        domain = domain[4:]

    return domain


def seed_sources() -> None:
    Base.metadata.create_all(
        bind=engine
    )

    added_count = 0
    existing_count = 0

    with SessionLocal() as db:
        for source_data in RWANDA_SOURCES:
            monitor_url = source_data[
                "monitor_url"
            ]

            existing = db.scalar(
                select(Source).where(
                    Source.monitor_url
                    == monitor_url
                )
            )

            if existing is not None:
                existing_count += 1

                existing.is_active = True
                existing.is_approved = True

                continue

            source = Source(
                organisation_name=(
                    source_data[
                        "organisation_name"
                    ]
                ),
                base_url=(
                    source_data[
                        "base_url"
                    ]
                ),
                monitor_url=monitor_url,
                domain=normalise_domain(
                    source_data[
                        "base_url"
                    ]
                ),
                source_type=(
                    source_data[
                        "source_type"
                    ]
                ),
                discovered_from=(
                    "Initial Rwanda source register"
                ),
                discovery_query=None,
                confidence_score=100,
                is_active=True,
                is_approved=True,
            )

            db.add(source)
            added_count += 1

        db.commit()

    print(
        f"New sources added: {added_count}"
    )

    print(
        f"Existing sources retained: "
        f"{existing_count}"
    )


if __name__ == "__main__":
    seed_sources()