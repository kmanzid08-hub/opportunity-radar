"""Metadata-only boundary, not document storage.

There is currently no company document library in this application. Unknown
inventory must not be confused with a known-empty, authoritative inventory.
A future library adapter can supply the same contract without exposing content.
"""

from datetime import date

from pydantic import Field

from app.intelligence.readiness_schemas import Availability
from app.intelligence.schemas import IntelligenceSchema


class CompanyDocument(IntelligenceSchema):
    record_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    availability: Availability
    valid_until: date | None = None


class CompanyDocumentInventory(IntelligenceSchema):
    company_id: int = Field(ge=1)
    complete: bool = False
    documents: list[CompanyDocument] = Field(default_factory=list)


def load_company_documents(
    company_ids: list[int],
) -> dict[int, CompanyDocumentInventory]:
    """No library exists: absence of knowledge is UNKNOWN, not KNOWN_MISSING."""
    return {
        company_id: CompanyDocumentInventory(company_id=company_id)
        for company_id in company_ids
    }


def document_snapshot(
    inventory: CompanyDocumentInventory, today: date
) -> CompanyDocumentInventory:
    """A certificate expiring invalidates the readiness cache without exposing files."""
    return inventory.model_copy(
        update={
            "documents": sorted(
                [
                    document.model_copy(
                        update={"availability": Availability.KNOWN_MISSING}
                    )
                    if document.valid_until is not None
                    and document.valid_until < today
                    and document.availability is Availability.KNOWN_AVAILABLE
                    else document
                    for document in inventory.documents
                ],
                key=lambda document: document.record_id,
            ),
        }
    )
