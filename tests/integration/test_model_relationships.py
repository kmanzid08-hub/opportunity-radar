from __future__ import annotations

from sqlalchemy import inspect, select
from sqlalchemy.orm import configure_mappers

from app.database import Base
from app.lead_models import Lead
from app.models import Opportunity, Source  # noqa: F401
from app.proposal_models import Proposal


def test_all_declared_models_configure_together() -> None:
    configure_mappers()

    assert set(Base.metadata.tables) == {
        "leads",
        "opportunities",
        "proposals",
        "sources",
    }


def test_opportunity_relationships_are_paired_one_to_one() -> None:
    opportunity_relationships = inspect(
        Opportunity
    ).relationships
    lead_relationship = opportunity_relationships["lead"]
    proposal_relationship = opportunity_relationships["proposal"]

    assert lead_relationship.mapper.class_ is Lead
    assert lead_relationship.back_populates == "opportunity"
    assert lead_relationship.uselist is False
    assert inspect(Lead).relationships[
        "opportunity"
    ].back_populates == "lead"
    assert inspect(Lead).relationships[
        "opportunity"
    ].uselist is False

    assert proposal_relationship.mapper.class_ is Proposal
    assert proposal_relationship.back_populates == "opportunity"
    assert proposal_relationship.uselist is False
    assert inspect(Proposal).relationships[
        "opportunity"
    ].back_populates == "proposal"
    assert inspect(Proposal).relationships[
        "opportunity"
    ].uselist is False

    assert Lead.__table__.c.opportunity_id.unique is True
    assert Proposal.__table__.c.opportunity_id.unique is True


def test_one_to_one_relationships_persist_in_isolated_sqlite(
    isolated_session_factory,
) -> None:
    opportunity = Opportunity(
        organisation_name="Example Buyer",
        title="Example request for proposals",
        category="Consulting",
        description="Fabricated relationship test opportunity.",
        source_name="Example Portal",
        source_url="https://example.test/notices/relationship-test",
        match_reason="relationship test",
        match_score=60,
    )
    opportunity.lead = Lead()
    opportunity.proposal = Proposal()

    with isolated_session_factory() as session:
        session.add(opportunity)
        session.commit()
        opportunity_id = opportunity.id

    with isolated_session_factory() as session:
        stored = session.scalar(
            select(Opportunity).where(
                Opportunity.id == opportunity_id
            )
        )

        assert stored is not None
        assert stored.lead is not None
        assert stored.lead.opportunity is stored
        assert stored.proposal is not None
        assert stored.proposal.opportunity is stored
