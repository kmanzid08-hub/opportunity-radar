import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.controlled_runner import BudgetedGemini, RunLimits
from app.intelligence import downstream_diagnostics, readiness_engine, triage_engine
from app.intelligence.downstream_diagnostics import record_failure, validation_details
from app.intelligence.gemini_client import (
    GeminiAPIError,
    GeminiClient,
    GeminiConfig,
    GeminiRequestBudgetError,
    GeminiResponseError,
)
from app.intelligence.readiness_schemas import ReadinessAnalysis
from app.intelligence.triage_schemas import TriageResult
from app.models import CompanyOpportunityReadiness, CompanyOpportunityTriage

from .test_readiness import analysis, assessment, extraction
from .test_readiness import setup_readiness as setup_readiness
from .test_triage import result
from .test_triage import setup_triage as setup_triage


SECRET = "PRIVATE_CONTENT_API_KEY_PROMPT"


@pytest.fixture(autouse=True)
def enable_diagnostic_logger(monkeypatch):
    # Alembic logging configuration can disable pre-existing loggers in the suite.
    monkeypatch.setattr(downstream_diagnostics.logger, "disabled", False)


@pytest.mark.parametrize("nested", [False, True])
def test_enum_field_paths_safe_and_no_response_contents(nested, caplog):
    values = result().model_dump(mode="json")
    path = "priority"
    if nested:
        values["required_documents"] = [{"name": SECRET, "category": SECRET}]
        path = "required_documents.0.category"
    else:
        values["priority"] = SECRET
    with pytest.raises(ValidationError) as caught:
        TriageResult.model_validate(values)
    with caplog.at_level(logging.WARNING):
        failure = record_failure(caught.value, stage="TRIAGE", schema=TriageResult)
    assert failure.category == "LOCAL_VALIDATION_ERROR"
    assert failure.details == [
        {"field": path, "error": "enum", "reason": "unexpected enum value"}
    ]
    assert SECRET not in failure.serialize() + caplog.text
    assert path in caplog.text


def test_unknown_field_names_and_custom_error_messages_are_not_logged():
    values = result().model_dump()
    values[SECRET] = SECRET
    with pytest.raises(ValidationError) as caught:
        TriageResult.model_validate(values)
    details = validation_details(caught.value, TriageResult)
    assert details[0]["field"] == "<unknown>"
    assert details[0]["error"] == "extra_forbidden"
    assert SECRET not in json.dumps(details)
    assert SECRET not in json.dumps(
        validation_details(ValueError(SECRET), TriageResult)
    )


def test_nested_readiness_path():
    values = assessment(extraction(analysis())).model_dump(mode="json")
    values["items"][0]["availability"] = SECRET
    with pytest.raises(ValidationError) as caught:
        ReadinessAnalysis.model_validate(values)
    assert (
        validation_details(caught.value, ReadinessAnalysis)[0]["field"]
        == "items.0.availability"
    )


@pytest.mark.parametrize(
    "exception,category",
    [
        (GeminiAPIError(SECRET), "PROVIDER_ERROR"),
        (GeminiResponseError(SECRET), "STRUCTURED_OUTPUT_ERROR"),
        (GeminiRequestBudgetError(SECRET), "REQUEST_BUDGET_EXHAUSTED"),
        (SQLAlchemyError(SECRET), "PERSISTENCE_ERROR"),
        (ValueError(SECRET), "LOCAL_VALIDATION_ERROR"),
    ],
)
def test_failure_categories(exception, category, caplog):
    failure = record_failure(exception, stage="TRIAGE", schema=TriageResult)
    assert failure.category == category
    assert SECRET not in failure.serialize() + caplog.text


def test_sdk_structured_validation_preserves_safe_details(monkeypatch, caplog):
    values = result().model_dump(mode="json")
    values["priority"] = SECRET
    sdk = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=Mock(return_value=SimpleNamespace(parsed=values))
        )
    )
    client = GeminiClient(GeminiConfig(api_key=SECRET), client_factory=lambda **kw: sdk)
    monkeypatch.setattr(
        logging.getLogger("app.intelligence.gemini_client"), "disabled", False
    )
    with pytest.raises(GeminiResponseError) as caught:
        client.generate_structured(SECRET, TriageResult)
    failure = record_failure(caught.value, stage="TRIAGE", schema=TriageResult)
    assert failure.category == "STRUCTURED_OUTPUT_ERROR"
    assert failure.details[0]["field"] == "priority"
    assert failure.details[0]["error"] == "enum"
    assert SECRET not in caplog.text + failure.serialize()
    sdk.models.generate_content.assert_called_once()


@pytest.mark.parametrize(
    "exception,category",
    [
        (GeminiAPIError(SECRET), "PROVIDER_ERROR"),
        (GeminiResponseError(SECRET), "STRUCTURED_OUTPUT_ERROR"),
        (GeminiRequestBudgetError(SECRET), "REQUEST_BUDGET_EXHAUSTED"),
        (ValueError("Missing budget must remain unknown"), "LOCAL_VALIDATION_ERROR"),
    ],
)
def test_triage_records_distinct_errors_and_reanalysis_succeeds(
    setup_triage,
    isolated_session_factory,
    exception,
    category,
):
    opportunity_id, _, matcher, analyzer = setup_triage
    analyzer.side_effect = [exception, result()]
    outcome = triage_engine.triage_verified_opportunity(opportunity_id)[0]
    assert outcome.error_category == category
    with isolated_session_factory() as db:
        stored = db.scalar(select(CompanyOpportunityTriage))
        assert stored.result_data is None
        assert json.loads(stored.error_reason)["category"] == category
    assert (
        triage_engine.triage_verified_opportunity(opportunity_id)[0].status == "TRIAGED"
    )
    matcher.assert_called_once()


def test_real_local_rule_failure_has_safe_reason_and_no_success_cache(
    setup_triage,
    isolated_session_factory,
):
    opportunity_id, _, _, analyzer = setup_triage
    analyzer.return_value = result(budget_evidence="CONFIRMED")
    outcome = triage_engine.triage_verified_opportunity(opportunity_id)[0]
    assert outcome.error_category == "LOCAL_VALIDATION_ERROR"
    with isolated_session_factory() as db:
        stored = db.scalar(select(CompanyOpportunityTriage))
        assert stored.result_data is None
        detail = json.loads(stored.error_reason)["details"][0]
        assert detail["field"] == "budget_evidence"
        assert detail["error"] == "missing_budget"
    analyzer.return_value = result()
    assert (
        triage_engine.triage_verified_opportunity(opportunity_id)[0].status == "TRIAGED"
    )


@pytest.mark.parametrize("stage", ["extraction", "readiness"])
@pytest.mark.parametrize(
    "exception,category",
    [
        (GeminiAPIError(SECRET), "PROVIDER_ERROR"),
        (GeminiResponseError(SECRET), "STRUCTURED_OUTPUT_ERROR"),
        (GeminiRequestBudgetError(SECRET), "REQUEST_BUDGET_EXHAUSTED"),
        (ValueError(SECRET), "LOCAL_VALIDATION_ERROR"),
    ],
)
def test_readiness_retains_failure_category_and_can_retry(
    setup_readiness,
    isolated_session_factory,
    stage,
    exception,
    category,
):
    opportunity_id, _, extract, assess = setup_readiness
    if stage == "extraction":
        extract.side_effect = [exception, extraction(analysis())]
    else:
        assess.side_effect = [exception, assessment(extraction(analysis()))]
    outcome = readiness_engine.analyze_opportunity_readiness(opportunity_id)[0]
    assert outcome.error_category == category
    with isolated_session_factory() as db:
        stored = db.scalar(select(CompanyOpportunityReadiness))
        assert stored.result_data is None
        assert json.loads(stored.error_reason)["category"] == category
        assert SECRET not in stored.error_reason
    assert (
        readiness_engine.analyze_opportunity_readiness(opportunity_id)[0].status
        == "ANALYZED"
    )


def test_readiness_write_failure_is_distinct(setup_readiness, monkeypatch):
    opportunity_id, _, _, _ = setup_readiness
    monkeypatch.setattr(
        readiness_engine, "_persist", Mock(side_effect=RuntimeError(SECRET))
    )
    outcome = readiness_engine.analyze_opportunity_readiness(opportunity_id)[0]
    assert outcome.error_category == "PERSISTENCE_ERROR"
    assert outcome.summary is None


def test_runner_does_not_cache_downstream_result_before_domain_validation():
    backend = Mock()
    backend.generate_structured.return_value = result(budget_evidence="CONFIRMED")
    budget = BudgetedGemini(backend, RunLimits(), emit=lambda _: None)
    budget.generate_structured("synthetic", TriageResult)
    assert all(value is None for value in budget.seen.values())
    with pytest.raises(GeminiAPIError):
        budget.generate_structured("synthetic", TriageResult)
    assert budget.cache_hits == 0
    assert budget.calls["TRIAGE"] == 1
    # A fresh run can reanalyze the same fingerprint.
    BudgetedGemini(backend, RunLimits(), emit=lambda _: None).generate_structured(
        "synthetic", TriageResult
    )
    assert backend.generate_structured.call_count == 2
