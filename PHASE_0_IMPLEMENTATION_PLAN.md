# Phase 0 Objective

Phase 0 establishes a trustworthy description of how Opportunity Radar behaves today before any multi-tenant SaaS restructuring begins. The present application is a small FastAPI/Jinja2 monolith, but its behavior is spread across route functions, module-level SQLAlchemy sessions, startup side effects, synchronous scanners, direct schema creation, manual migration scripts, and several overlapping schedulers. Refactoring those boundaries without characterization tests would make it difficult to distinguish an intentional architectural change from a regression.

The objective is not to redesign the application, repair every known defect, or introduce the future tenant model. It is to:

- Capture the current behavior of the web routes, filtering, scoring, persistence, scanning parsers, source health, and automation configuration.
- Make tests safe by default so they cannot use the real local database, production PostgreSQL, API keys, schedulers, or live websites.
- Create a minimal repeatable quality workflow for future changes.
- Record known inconsistencies as explicit regression tests or documented limitations before correcting them in later, separately reviewed work.
- Establish a baseline that works locally and in CI without requiring Render, production credentials, or external network availability.

Characterization tests should initially assert what the code does, including surprising behavior. Tests for known defects should be named clearly and should explain when their expectation must change. They should not silently redefine desired future behavior.

# Current Testability Assessment

## Functions that are easy to test

The following functions are deterministic or nearly deterministic and can be tested without a database or network:

- `app.filters.normalise_text`
- `app.filters.contains_term`
- `app.filters.find_matches`
- `app.filters.build_searchable_text`
- `app.filters.is_procurement_document`
- Most of `app.filters.classify_opportunity`, up to the current `FilteredOpportunity` construction defect.
- `app.scanner.opportunity_has_expired`, with the limitation that it reads `date.today()`.
- `app.scanner.clean_organisation_name`
- `app.scanner.update_expiry_status`, using an in-memory `Opportunity` instance.
- `app.scanner.get_scanner_name`
- `app.discovery.source_repository.normalise_url`
- `app.discovery.source_repository.normalise_domain`
- `app.discovery.source_repository.get_base_url`
- `app.discovery.source_classifier.classify_source`
- `app.discovery.source_classifier.determine_source_type`
- `app.source_quality.SourceQualityEvaluator` methods that operate only on supplied values.
- `app.source_health.SourceHealthManager.calculate_priority`, using a constructed `Source`.
- `app.automation.parse_timestamp`
- `app.automation.is_due`
- `app.internal_scheduler.InternalScheduler._parse_datetime`
- `app.scanners.company_websites.CompanyWebsiteScanner` parsing and normalization helpers when supplied local BeautifulSoup documents.
- `app.scanners.job_in_rwanda.JobInRwandaScanner` link, title, organization, structured-data, deadline, and date helpers when supplied local BeautifulSoup documents.

Tests should preserve the distinctions among the multiple existing URL normalizers. Phase 0 should characterize them; it should not consolidate them yet.

## Functions that mix database, network, and business logic

The following areas require integration fixtures or test doubles because concerns are coupled:

- `app.main` imports `engine` and `SessionLocal`, calls `Base.metadata.create_all(bind=engine)` at import time, and opens `SessionLocal()` directly in route helpers and route functions.
- `app.scanner.run_scanner` performs schema checks, mutates source defaults, expires database rows, constructs live scanner objects, invokes network scans, classifies candidates, and persists results in one orchestration path.
- `app.scanner.save_opportunity` combines duplicate detection, field reconciliation, expiry rules, timestamps, and transaction commits.
- `app.scanner.ensure_sqlite_schema` combines runtime schema creation and SQLite-specific `ALTER TABLE`.
- `CompanyWebsiteScanner.scan` loads sources from the database, performs HTTP/robots requests, parses pages, updates health fields, and applies scheduling rules.
- `CompanyWebsiteScanner._scan_source` combines crawl traversal, same-domain policy, robots checks, HTTP, HTML parsing, document detection, and opportunity creation.
- `JobInRwandaScanner.scan` performs pagination, HTTP access, parsing, deduplication, detail enrichment, rate limiting, and fallback behavior.
- `app.source_discovery.RwandaSourceDiscovery` combines Brave Search access, hard-coded discovery rules, quality evaluation, and direct database writes.
- `app.discovery.search_discovery.run_search_discovery` is a second Brave Search orchestration path using a different environment variable and classifier/repository implementation.
- `app.source_health.SourceHealthManager.record_*` methods calculate health and commit directly through an imported `SessionLocal`.
- `app.discovery.source_repository.save_discovered_source` normalizes, deduplicates, updates, and commits directly.
- `app.scheduled_discovery` performs PostgreSQL-oriented state-table DDL and scheduling checks through a module-level engine.
- `app.jobs.run_website_discovery` starts a subprocess for `app.discover_sources`, although that module does not exist in the tracked repository.

Phase 0 should test smaller seams and persistence behavior directly. It should not call top-level live scanning or discovery functions.

## Routes requiring characterization

The current FastAPI application exposes these routes:

- `GET /`: dashboard rendering, text/category/status/deadline filters, counts, and ordering.
- `GET /opportunities/{opportunity_id}`: detail rendering, 404 behavior, and the side effect that changes `New` to `Seen`.
- `POST /opportunities/{opportunity_id}/update`: status and notes update, validation, expiry flag behavior, redirect, and 404.
- `POST /opportunities/{opportunity_id}/status`: quick status update, validation, expiry behavior, redirect, and 404.
- `POST /opportunities/{opportunity_id}/pipeline/add`: embedded lead-field initialization and redirect.
- `POST /opportunities/{opportunity_id}/pipeline/remove`: `is_lead` removal and redirect.
- `GET /pipeline`: inclusion of only `is_lead=True` opportunities and deadline ordering.
- `POST /opportunities/{opportunity_id}/pipeline/update`: priority/status validation, date parsing, lead-to-opportunity status mapping, and rejection when not in the pipeline.
- `POST /scan`: direct scanner invocation and redirect.

The minimum suite should initially cover startup and the three requested GET views. Mutation-route characterization should follow in small additions. `POST /scan` must be tested only with `app.main.run_scanner` replaced by a spy; it must never start scanners.

## Scanners requiring recorded HTML fixtures

`CompanyWebsiteScanner` requires sanitized fixtures representing:

- A generic organization home or procurement index page with relevant and irrelevant links.
- A procurement detail page with title, description, procurement evidence, and deadline.
- A page with missing metadata to characterize fallback title/description behavior.
- A document link to characterize document-opportunity creation without downloading the document.
- Optional malformed or very short HTML to characterize rejection.

`JobInRwandaScanner` requires sanitized fixtures representing:

- A section/listing page containing valid `/job/...` links, duplicates, irrelevant links, title previews, and organization previews.
- A detail page containing title, organization, description, and a recognizable deadline.
- A detail page with JSON-LD data.
- A detail page with missing organization information to characterize fallback logic.
- Optional malformed structured data to confirm safe handling.

Fixtures must be minimal derived examples, not complete copyrighted pages. Personal information, tracking values, scripts, analytics identifiers, and unnecessary text must be removed.

## Database operations requiring integration tests

Integration tests against temporary SQLite databases are needed for:

- SQLAlchemy table creation for `Opportunity` and `Source`.
- Insert, query, update, transaction commit, and unique `source_url` behavior.
- `app.scanner.save_opportunity` insertion and duplicate-by-URL update behavior.
- Expiry-state persistence.
- Dashboard counts and filters.
- Detail-route `New` to `Seen` persistence.
- Pipeline inclusion and embedded lead-field persistence.
- `SourceHealthManager.record_scan_started`, `record_success`, and `record_failure`.
- `app.discovery.source_repository.save_discovered_source` normalization and duplicate behavior.

`ensure_sqlite_schema` should receive a focused test only after the ordinary persistence suite is stable, because it executes runtime DDL. Its test database must be disposable.

## Overlapping scheduling paths

The repository currently has several ways to run similar work:

1. `app.main.lifespan` starts `app.internal_scheduler.scheduler` unless `ENABLE_INTERNAL_SCHEDULER` is false.
2. `app.internal_scheduler` runs tender scans daily and website discovery every five days, using JSON state and a process lock.
3. `app.automation` independently applies daily/five-day intervals using the same repository state filename and subprocess commands.
4. GitHub Actions runs `python -m app.scanner` daily.
5. GitHub Actions runs `python -m app.scheduled_discovery` daily; that module uses a database state table to enforce a five-day interval.
6. `POST /scan` invokes `run_scanner()` synchronously from a web request.
7. `app.jobs.run_website_discovery` references the untracked/nonexistent module `app.discover_sources`, while other paths use `app.source_discovery` or `app.scheduled_discovery`.

Phase 0 should document and safely test configuration/decision helpers. It should not choose or remove a scheduling path.

## Model and contract inconsistencies to document

Phase 0 must record, but not redesign:

- `Opportunity` contains embedded lead fields (`is_lead`, `assigned_to`, `lead_status`, `lead_priority`, action dates, and notes).
- A separate `Lead` model defines a one-to-one `opportunity` relationship with `back_populates="lead"`, but `Opportunity` does not define the matching `lead` relationship.
- A separate `Proposal` model similarly expects `Opportunity.proposal`, which is not defined.
- Lead status/priority constants are duplicated in `app.models` and `app.lead_models`.
- `FilteredOpportunity` defines flattened fields, but `classify_opportunity` constructs it with an unsupported `raw=` argument and omits required flattened fields. A relevant candidate therefore raises `TypeError` instead of returning a scored object.
- The active routes use embedded lead fields and do not use the separate `Lead` or `Proposal` models.
- Model creation and direct schema-alteration scripts coexist without a single migration history.
- `scheduled_discovery` uses `ON CONFLICT`, which is not a general proof of the promised SQLite/PostgreSQL compatibility without dialect-specific tests.
- Different modules use `BRAVE_API_KEY` and `BRAVE_SEARCH_API_KEY`.

# Proposed Test Structure

The initial structure should be:

```text
tests/
├── conftest.py
├── unit/
│   ├── test_automation_timing.py
│   ├── test_filter_helpers.py
│   ├── test_opportunity_classifier.py
│   ├── test_scanner_helpers.py
│   ├── test_source_classifier.py
│   ├── test_source_health.py
│   ├── test_source_quality.py
│   └── test_url_normalization.py
├── integration/
│   ├── test_database_persistence.py
│   ├── test_opportunity_persistence.py
│   ├── test_source_health_persistence.py
│   └── test_source_repository.py
├── routes/
│   ├── test_startup.py
│   ├── test_dashboard.py
│   ├── test_opportunity_detail.py
│   ├── test_pipeline.py
│   └── test_opportunity_mutations.py
├── scanners/
│   ├── test_company_websites.py
│   ├── test_job_in_rwanda.py
│   └── test_network_isolation.py
├── workflows/
│   └── test_github_actions.py
└── fixtures/
    ├── company_websites/
    │   ├── procurement_index.html
    │   ├── procurement_detail.html
    │   ├── sparse_page.html
    │   └── document_index.html
    └── job_in_rwanda/
        ├── section_page.html
        ├── detail_page.html
        ├── detail_json_ld.html
        └── detail_missing_organisation.html
```

The folders need no `__init__.py` unless imports later require it. Tests should import application modules, not duplicate production algorithms. Shared factories should stay in `tests/conftest.py` until their size justifies `tests/factories.py`.

`tests/conftest.py` should provide:

- Pre-import environment safety initialization.
- A session-scoped safe temporary root.
- A per-test temporary SQLite engine and `sessionmaker`.
- Table setup/teardown and engine disposal.
- A factory for valid `Opportunity`, `Source`, `RawOpportunity`, and `FilteredOpportunity` objects.
- A `TestClient` fixture with internal scheduling disabled.
- Monkeypatching of imported `SessionLocal` references in the modules under test.
- An autouse live-network denial fixture.
- Helpers to read fixture HTML as UTF-8 and build BeautifulSoup objects.

# Minimum Test Suite

## 1. Application startup

- **Purpose:** Prove the FastAPI application imports and enters/exits its lifespan using a safe test database without starting automation.
- **Target:** `app.main.app`, `app.main.lifespan`, and the root route as a smoke request.
- **Fixtures:** Pre-import test environment, temporary SQLite database, `TestClient`, scheduler start/stop spies.
- **Expected assertions:** Application metadata is available; client context opens; `GET /` returns 200; scheduler spies are not called when `ENABLE_INTERNAL_SCHEDULER=false`; no real `opportunities.db` is created or changed.
- **Type:** Integration/route.
- **Risks or limitations:** Importing `app.main` calls `Base.metadata.create_all`; safety environment configuration must happen before test-module imports.

## 2. Dashboard route

- **Purpose:** Characterize dashboard rendering, counts, filtering, and empty behavior.
- **Target:** `app.main.home`, `get_status_counts`, and `get_categories` through `GET /`.
- **Fixtures:** Temporary SQLite database with opportunities covering categories, statuses, expired/open/no-deadline states, and distinct titles.
- **Expected assertions:** 200 response; expected titles and counts appear; `q`, `category`, `status`, and each deadline filter include/exclude the expected rows; empty database renders the empty state.
- **Type:** Route integration.
- **Risks or limitations:** Assertions against stable semantic text are preferable to full-page snapshots, which would be brittle.

## 3. Opportunity detail route

- **Purpose:** Preserve detail rendering, missing-record behavior, and the current read-side status mutation.
- **Target:** `app.main.opportunity_detail` through `GET /opportunities/{id}`.
- **Fixtures:** Temporary SQLite opportunity in `New` status plus session factory.
- **Expected assertions:** Existing row returns 200 and displays title/source/description; status becomes `Seen` in the database; unknown ID returns 404 with `Opportunity not found`.
- **Type:** Route integration.
- **Risks or limitations:** Marking a record `Seen` on GET is a current side effect, not necessarily desired future REST behavior.

## 4. Pipeline route

- **Purpose:** Characterize which rows appear in the current embedded-field pipeline.
- **Target:** `app.main.pipeline_page` through `GET /pipeline`.
- **Fixtures:** Opportunities with `is_lead=True` and `False`, mixed deadlines, priorities, and statuses.
- **Expected assertions:** 200 response; only lead rows appear; pipeline count is correct; stable ordering assertions use distinguishable deadlines.
- **Type:** Route integration.
- **Risks or limitations:** This characterizes embedded `Opportunity` fields, not the unused separate `Lead` model.

## 5. Opportunity filtering

- **Purpose:** Capture procurement-evidence gates, employment rejection, generic-page rejection, and category selection.
- **Target:** `normalise_text`, `contains_term`, `is_procurement_document`, and `classify_opportunity` in `app.filters`.
- **Fixtures:** Small `RawOpportunity` factory with irrelevant service page, job vacancy, procurement notice, and procurement-document URL cases.
- **Expected assertions:** Irrelevant/generic/employment-only inputs return `None`; helper matching respects word boundaries; document evidence is recognized; qualifying input reaches the currently broken constructor path.
- **Type:** Unit.
- **Risks or limitations:** The positive path cannot currently return `FilteredOpportunity` because of the documented contract defect.

## 6. Relevance scoring

- **Purpose:** Freeze the current score components and acceptance threshold independently of future configurable scoring.
- **Target:** `CATEGORY_RULES`, `MINIMUM_ACCEPTANCE_SCORE`, and `classify_opportunity`.
- **Fixtures:** Carefully controlled `RawOpportunity` cases varying strong service terms, procurement terms, deadlines, generic terms, and employment terms.
- **Expected assertions:** Inputs below evidence/score thresholds are rejected; score-contributing cases are calculated according to current weights. Until the contract defect is fixed, a test may independently reproduce the documented arithmetic from matched-term outputs or assert the qualifying path's failure after proving the matched signals.
- **Type:** Unit characterization.
- **Risks or limitations:** Avoid copying the entire algorithm into tests. Use a few hand-calculable examples; update deliberately when the scoring contract is repaired.

## 7. URL normalization

- **Purpose:** Preserve deduplication-sensitive URL behavior and expose differences among current normalizers.
- **Target:** `app.discovery.source_repository.normalise_url`, `normalise_domain`, `get_base_url`, plus focused characterization of `CompanyWebsiteScanner._normalise_url`.
- **Fixtures:** Parameterized URL strings with `www`, case differences, trailing slashes, tracking parameters, sorted query parameters, fragments, and missing schemes.
- **Expected assertions:** Tracking parameters are removed by the repository normalizer; retained query parameters are sorted; fragments are removed; domain/base URL match current behavior; scanner normalization behavior is recorded separately.
- **Type:** Unit.
- **Risks or limitations:** Different normalizers intentionally or accidentally differ. Phase 0 must not assert that they are interchangeable.

## 8. Duplicate opportunity handling

- **Purpose:** Characterize URL-based identity, insert/update behavior, and return values.
- **Target:** `app.scanner.save_opportunity`.
- **Fixtures:** Temporary SQLite database and two valid `FilteredOpportunity` objects sharing `source_url` but differing in title, description, category, score, deadline, and organization.
- **Expected assertions:** First call returns `True` and creates one row; second returns `False`; row count remains one; updateable fields and `last_seen_at` change; source URL remains unique; workflow state is not unexpectedly reset.
- **Type:** Integration.
- **Risks or limitations:** Timestamp assertions need ranges or an injected/monkeypatched clock; exact microseconds are brittle.

## 9. SQLite database persistence

- **Purpose:** Prove the declared models work in a disposable local SQLite database.
- **Target:** `app.database.Base`, `Opportunity`, `Source`, SQLAlchemy engine/session behavior.
- **Fixtures:** File-backed SQLite database under pytest's temporary directory.
- **Expected assertions:** Tables are created; rows commit and reload; date, boolean, score, and timezone-related fields round-trip as currently implemented; duplicate unique URLs raise an integrity error and rollback leaves the session usable.
- **Type:** Integration.
- **Risks or limitations:** SQLite does not prove PostgreSQL DDL, types, locking, timezone, `ON CONFLICT`, or future RLS behavior.

## 10. Source health calculations

- **Purpose:** Characterize priority weighting, bounds, failure penalties, success bonus, auto-disabled behavior, and persisted health transitions.
- **Target:** `SourceHealthManager.calculate_priority`, then `record_success` and `record_failure`.
- **Fixtures:** Constructed `Source` objects for unit cases; temporary SQLite persisted Source for integration cases.
- **Expected assertions:** Formula uses 55% confidence, 20% URL relevance, capped yield bonus, capped failure penalty, success bonus, range 0–100, and zero for auto-disabled; record methods update counters/timestamps/status as expected.
- **Type:** Unit first, small integration follow-up.
- **Risks or limitations:** `datetime.utcnow()` is embedded; assert ordering/presence rather than exact timestamps unless monkeypatched.

## 11. Website scanner parsing with local HTML

- **Purpose:** Characterize generic website link discovery and opportunity extraction without HTTP.
- **Target:** `CompanyWebsiteScanner._extract_relevant_links`, `_build_page_opportunity`, `_extract_title`, `_extract_description`, `_extract_deadline`, and document helpers.
- **Fixtures:** Sanitized files under `tests/fixtures/company_websites/`; BeautifulSoup created directly from those files; request delays disabled.
- **Expected assertions:** Relevant same-domain links are selected; excluded career/social/login links are ignored; title/description/deadline are extracted; non-opportunity page returns `None`; document link yields current title/source behavior.
- **Type:** Scanner unit/fixture test.
- **Risks or limitations:** Private methods are tested because they are the safest current seams. Full crawl traversal remains untested until network and persistence are separated.

## 12. Job in Rwanda scanner parsing with local HTML

- **Purpose:** Freeze listing discovery, detail extraction, fallbacks, structured data, and deadline parsing without contacting Job in Rwanda.
- **Target:** `JobInRwandaScanner._extract_listing_links`, `_build_opportunity`, `_extract_detail_*`, `_extract_structured_data`, `_extract_deadline`, and `_normalise_listing_url`.
- **Fixtures:** Sanitized section/detail/JSON-LD/missing-organization files; `_get_soup` monkeypatched to return fixture soups by fake URL; waits disabled.
- **Expected assertions:** Valid listing links are normalized and deduplicated; invalid links are ignored; extracted `RawOpportunity` has expected title, organization, description, URL, and deadline; fallback behavior matches current code.
- **Type:** Scanner unit/fixture test.
- **Risks or limitations:** Site markup may later change. Fixture updates must be reviewed as parser-contract changes, not automatic refreshes from the live site.

## 13. FilteredOpportunity/classifier contract regression

- **Purpose:** Make the known classifier output defect visible before any fix.
- **Target:** `app.schemas.FilteredOpportunity` and `app.filters.classify_opportunity`.
- **Fixtures:** One `RawOpportunity` with enough strong service and procurement evidence to pass the score threshold.
- **Expected assertions:** Direct construction with all declared flattened fields succeeds; calling `classify_opportunity` with the qualifying raw input currently raises `TypeError` mentioning the unsupported `raw` argument. The test name and comment identify this as a known regression characterization.
- **Type:** Unit regression.
- **Risks or limitations:** This is intentionally a passing test for broken current behavior. When a separately approved fix is made, change it to assert a complete returned object rather than deleting the test.

## 14. GitHub workflow YAML validation

- **Purpose:** Catch invalid YAML and accidental loss of essential safety/schedule settings.
- **Target:** `.github/workflows/daily-tender-scan.yml` and `website-discovery.yml`.
- **Fixtures:** Repository file paths and a YAML parser configured not to misinterpret the YAML key `on` as a boolean.
- **Expected assertions:** Both files parse; each has `workflow_dispatch`; Python version and install step exist; internal scheduler is false; tender command is `python -m app.scanner`; discovery command is `python -m app.scheduled_discovery`; concurrency groups differ; secret values are references rather than literals.
- **Type:** Static configuration test.
- **Risks or limitations:** Basic YAML parsing does not fully validate GitHub Actions semantics. Add `actionlint` later if adopted and pinned in CI.

## 15. Confirmation that tests never contact live websites

- **Purpose:** Make network isolation fail closed rather than relying on developer discipline.
- **Target:** Entire pytest process, with specific proof tests around both scanner classes.
- **Fixtures:** Autouse network blocker patching socket connection attempts and common `requests` entry points; local TestClient remains in-process.
- **Expected assertions:** An attempted `requests.get("https://example.invalid")` or scanner `_get_soup` call without a test double raises a clear `LiveNetworkBlocked`-style assertion immediately; all scanner fixture tests pass without exceptions from the guard.
- **Type:** Test-infrastructure regression.
- **Risks or limitations:** Patch at a low enough layer to cover `requests`, `urllib.robotparser`, and accidental new clients. If a future test legitimately needs a local service, it should require an explicit narrowly scoped opt-in.

# Dependencies

The current `requirements.txt` contains runtime packages only:

- Beautiful Soup for HTML parsing.
- FastAPI, Jinja2, multipart support, and Uvicorn for the web application.
- SQLAlchemy and PostgreSQL `psycopg`.
- Requests and dotenv.

Do not add testing tools to runtime production requirements. Create a separate `requirements-dev.txt` in the implementation task, using compatible, reviewed version ranges.

Minimum justified development dependencies:

- **`pytest`**: test runner, fixtures, parameterization, monkeypatching, and exception assertions.
- **`httpx`**: required by FastAPI/Starlette `TestClient` and useful for in-process ASGI route tests. It must not be used for live network tests.
- **`pytest-cov`**: produces a baseline and identifies uncharacterized areas. Start without a hard fail-under percentage.
- **`ruff`**: one fast tool for linting and formatting tests, then gradually the existing application.
- **`PyYAML`**: parses the two workflow files for syntax and repository-specific structural assertions. Use a loader/configuration that handles GitHub Actions' `on` key correctly.

Not initially required:

- `pytest-asyncio`, because current route characterization can use synchronous `TestClient`.
- `requests-mock` or `responses`, because scanner parsing should use local HTML and monkeypatched parser seams; the default policy is no network.
- `freezegun`, because current time assertions can use bounded comparisons or small monkeypatches.
- `mypy`, because a repository-wide strict type gate would create a large unrelated baseline. It may be introduced later in advisory mode for selected modules.
- Browser automation, because current views are server-rendered and route-level HTML assertions cover Phase 0.

# Database Test Strategy

## Temporary SQLite databases

Use a file-backed SQLite database under pytest's temporary directory for each test or logical test group:

```text
sqlite+pysqlite:///C:/.../pytest-temp/test-opportunity-radar.db
```

File-backed SQLite avoids the multi-connection and thread surprises of a default `:memory:` database with FastAPI's `TestClient`. Build the URL from `Path.as_posix()` on Windows. Configure `check_same_thread=False`, create only declared tables, and dispose the engine after the test.

Never place a test database at repository root, never reuse `opportunities.db`, and never copy a real database into the suite.

## Session isolation

Create a new engine and `sessionmaker` per test by default. Create tables before yielding the fixture. After the test:

1. Roll back any open session.
2. Close sessions.
3. Drop tables only in the temporary test database if needed.
4. Dispose the engine.
5. Let pytest remove its temporary directory.

Transaction rollback fixtures can be introduced later for speed, but simplicity and reliable isolation are more important for the first suite.

## Application database override

The conventional FastAPI pattern is:

```python
app.dependency_overrides[get_db] = override_get_db
```

That override is not sufficient in the current repository because the routes do not depend on `get_db`; they call the `SessionLocal` name imported into `app.main`. Similarly, scanner and source-health modules hold their own imported references.

For initial characterization without production refactoring:

- Force a safe temporary `DATABASE_URL` before importing any `app` database or main module.
- Monkeypatch `app.main.SessionLocal` to the per-test session factory.
- For persistence tests, patch the exact module under test, such as `app.scanner.SessionLocal`, `app.source_health.SessionLocal`, `app.discovery.source_repository.SessionLocal`, or `app.scanners.company_websites.SessionLocal`.
- Patch module-level `engine` references only for tests that explicitly exercise engine-dependent functions.
- Clear `app.dependency_overrides` after route tests even though current routes do not use it, preventing future test leakage when routes adopt `get_db`.

A later small refactor may introduce `Depends(get_db)`, but it must occur only after these characterization tests exist.

## What SQLite cannot prove

SQLite tests cannot prove:

- PostgreSQL driver and connection behavior.
- PostgreSQL `TIMESTAMP WITH TIME ZONE` semantics.
- `ON CONFLICT` behavior in all current raw SQL.
- Concurrent updates, row locking, transaction isolation, or connection-pool behavior.
- PostgreSQL-specific DDL and migration correctness.
- Case-insensitive search equivalence for all locales.
- Numeric, boolean, date, and server-default parity in every edge case.
- Future Row-Level Security or tenant isolation.

## PostgreSQL integration timing

Add ephemeral PostgreSQL integration tests in Phase 1, when a migration framework and CI service container are introduced. They should use a disposable database created for the CI job, a dedicated test role, and no network route to production. Before any SaaS tenancy work, PostgreSQL tests must cover migrations, constraints, timezone behavior, dialect-specific SQL, and eventually RLS.

## Preventing production database access

`tests/conftest.py` must overwrite, not merely default, `DATABASE_URL` before importing application modules. It must then verify:

- Scheme is SQLite for the initial suite.
- Resolved database file is inside pytest's temporary directory.
- Hostnames such as Render endpoints cannot appear.
- An unsafe URL causes immediate test-session failure.

CI test jobs must set their own explicit test URL and must not expose production database secrets. Workflow tests should inspect YAML as text/configuration only; they must never evaluate secret references.

# Scanner Fixture Strategy

1. Create minimal HTML manually or from a legally obtained page sample.
2. Retain only structural elements needed by the parser: selected anchors, headings, metadata, description blocks, date text, and JSON-LD fields.
3. Replace organization names, people, emails, phone numbers, IDs, and tracking values with fictional values.
4. Remove scripts, styles, analytics, advertisements, unrelated navigation, and long copyrighted descriptions.
5. Add a short HTML comment stating that the fixture is sanitized and identifying the behavior it represents, not the real source content.
6. Store fixtures as UTF-8 under the scanner-specific directory.
7. Load fixture text directly and construct `BeautifulSoup`; never load a fixture through HTTP.
8. Monkeypatch `_get_soup` with a deterministic fake URL-to-fixture mapping when testing an orchestration helper.
9. Set scanner request delays to zero in tests.
10. Do not include an automated fixture-refresh script in Phase 0. Any future refresh must be manual, reviewed, sanitized, and clearly diffed.
11. Test missing fields, malformed JSON-LD, duplicate links, and irrelevant links as well as a normal success case.
12. Treat fixture changes as parser contract changes requiring review.

No CI test should depend on DNS, internet access, current website markup, Brave Search, robots.txt, or a real API key.

# Configuration Safety

The test harness should fail closed with these safeguards:

- Set `ENABLE_INTERNAL_SCHEDULER=false` before importing `app.main`.
- Force `DATABASE_URL` to a generated temporary SQLite path before importing `app.database`.
- Remove or blank `BRAVE_API_KEY` and `BRAVE_SEARCH_API_KEY`.
- Remove or replace `TENDER_SCAN_COMMAND` and `SOURCE_DISCOVERY_COMMAND` so no subprocess can invoke scanning.
- Monkeypatch `subprocess.run` in any automation/job test.
- Apply an autouse socket/network blocker. Patch scanner `_get_soup` methods only with local fixture readers.
- Spy on `scheduler.start`, `scheduler.stop`, and `run_scanner` in relevant route tests.
- Never call `/scan` without replacing `app.main.run_scanner`.
- Ensure current working-directory `opportunities.db` modification time and hash are never part of test setup; tests should not open it at all.
- Do not load the repository `.env`. Tests set required variables explicitly before importing modules; dotenv-dependent modules should not be imported unless the test isolates them.
- Do not expose production secrets to the test job. Use fake values only where a parser requires a non-empty token.
- No email service currently exists, but the safety fixture should reserve a no-send provider/flag and block SMTP sockets so future additions fail safely.
- Use fake external URLs such as `https://procurement.example.test/...` in fixtures. They are identifiers only and must never be requested.
- Mark any future network-capable test explicitly and exclude it from the default suite; Phase 0 should contain no such test.

# Quality Gates

Introduce gates incrementally so Phase 0 provides signal without blocking all work on unrelated legacy findings.

## Tests

```powershell
python -m pytest -q
```

Run focused subsets while developing:

```powershell
python -m pytest tests/unit -q
python -m pytest tests/routes -q
python -m pytest tests/integration -q
python -m pytest tests/scanners -q
python -m pytest tests/workflows -q
```

## Coverage

```powershell
python -m pytest --cov=app --cov-report=term-missing --cov-report=xml
```

Record the initial percentage but do not initially use `--cov-fail-under`. Once stable, ratchet coverage upward based on the baseline and critical modules rather than selecting an arbitrary high number. Route, persistence, filtering, and scanner parsing coverage matters more than aggregate percentage.

## Formatting and linting

Start by gating new test code:

```powershell
python -m ruff check tests
python -m ruff format --check tests
```

Run application linting in report-only mode first:

```powershell
python -m ruff check app
python -m ruff format --check app
```

Do not reformat the whole application as part of Phase 0 test foundation. Adopt or fix application rules in small separately reviewed changes.

## Optional type checking

Do not make repository-wide mypy a first gate. After test foundations exist, evaluate it on pure modules first:

```powershell
python -m mypy app/schemas.py app/filters.py app/source_health.py
```

Only add `mypy` and configuration after documenting the initial error baseline and choosing justified strictness.

## Workflow validation

The repository test is:

```powershell
python -m pytest tests/workflows/test_github_actions.py -q
```

If `actionlint` is later adopted as a pinned CI tool:

```powershell
actionlint
```

PyYAML structural tests are the minimum initial gate; `actionlint` is a valuable later semantic check.

# Implementation Steps

## Step 1 — Add isolated development-test configuration

- **Exact goal:** Define test-only dependencies and pytest discovery without touching runtime code.
- **Files likely to be created or modified:** Create `requirements-dev.txt` and `pytest.ini` or an equivalent small pytest configuration file.
- **Commands to run:** `python -m pip install -r requirements-dev.txt` only after explicit approval; then `python -m pytest --collect-only`.
- **Validation:** Dependencies are separate from `requirements.txt`; pytest discovers the intended tree; no application module is imported merely to collect empty tests.
- **Rollback approach:** Remove the two new configuration files; no application or database state is affected.

## Step 2 — Build the fail-closed test harness

- **Exact goal:** Guarantee a temporary SQLite database, disabled scheduler/API keys/subprocesses, and denied live network before importing application modules.
- **Files likely to be created or modified:** Create `tests/conftest.py` and `tests/scanners/test_network_isolation.py`.
- **Commands to run:** `python -m pytest tests/scanners/test_network_isolation.py -q`.
- **Validation:** Deliberate HTTP/socket attempt fails with the expected test error; safe temporary database path is reported only in pytest temp space; `opportunities.db` remains untouched.
- **Rollback approach:** Remove the two test files; no production file changed.

## Step 3 — Add startup and empty-view smoke tests

- **Exact goal:** Characterize safe application startup plus empty dashboard and pipeline rendering.
- **Files likely to be created or modified:** Create `tests/routes/test_startup.py`, `test_dashboard.py`, and `test_pipeline.py`.
- **Commands to run:** `python -m pytest tests/routes/test_startup.py tests/routes/test_dashboard.py tests/routes/test_pipeline.py -q`.
- **Validation:** All responses are 200; scheduler is not started; empty states render; no network or real database access occurs.
- **Rollback approach:** Remove only these route test files.

## Step 4 — Add seeded route characterization

- **Exact goal:** Verify dashboard filters/counts, detail rendering/404/Seen transition, and pipeline selection using temporary rows.
- **Files likely to be created or modified:** Extend `tests/conftest.py`; modify route tests; create `tests/routes/test_opportunity_detail.py`.
- **Commands to run:** `python -m pytest tests/routes -q`.
- **Validation:** Seeded assertions and database side effects match current route behavior; every test receives isolated data.
- **Rollback approach:** Revert the route tests and factory additions; temporary databases are disposable.

## Step 5 — Characterize filter helpers and the classifier defect

- **Exact goal:** Freeze procurement gates, word matching, rejection cases, selected score examples, and the `FilteredOpportunity(raw=...)` failure.
- **Files likely to be created or modified:** Create `tests/unit/test_filter_helpers.py` and `tests/unit/test_opportunity_classifier.py`.
- **Commands to run:** `python -m pytest tests/unit/test_filter_helpers.py tests/unit/test_opportunity_classifier.py -q`.
- **Validation:** Irrelevant inputs reject correctly; helper matches are stable; the known positive-path `TypeError` is explicit and narrowly asserted.
- **Rollback approach:** Remove these unit tests; no production behavior changes.

## Step 6 — Characterize database persistence and duplicates

- **Exact goal:** Test model round trips and `save_opportunity` insert/update semantics in disposable SQLite.
- **Files likely to be created or modified:** Create `tests/integration/test_database_persistence.py` and `test_opportunity_persistence.py`; extend factories in `conftest.py`.
- **Commands to run:** `python -m pytest tests/integration/test_database_persistence.py tests/integration/test_opportunity_persistence.py -q`.
- **Validation:** Row counts, unique constraints, updates, return values, and rollback behavior are correct; only temp files are created.
- **Rollback approach:** Remove the integration test files and fixture additions.

## Step 7 — Characterize URL and source behavior

- **Exact goal:** Freeze URL normalization, source classification, source priority, and health persistence.
- **Files likely to be created or modified:** Create `tests/unit/test_url_normalization.py`, `test_source_classifier.py`, `test_source_health.py`, and `tests/integration/test_source_health_persistence.py`.
- **Commands to run:** `python -m pytest tests/unit/test_url_normalization.py tests/unit/test_source_classifier.py tests/unit/test_source_health.py tests/integration/test_source_health_persistence.py -q`.
- **Validation:** Parameterized normalization and formula cases pass; health database transitions persist in isolation.
- **Rollback approach:** Remove these tests; no source records outside pytest are touched.

## Step 8 — Add generic website fixtures and parser tests

- **Exact goal:** Characterize CompanyWebsiteScanner parsing without invoking HTTP, robots.txt, database source selection, or sleep.
- **Files likely to be created or modified:** Create `tests/scanners/test_company_websites.py` and sanitized files under `tests/fixtures/company_websites/`.
- **Commands to run:** `python -m pytest tests/scanners/test_company_websites.py -q`.
- **Validation:** All cases use local fixture text; network guard remains active; relevant link/title/description/deadline assertions pass.
- **Rollback approach:** Remove the test and its fixture directory.

## Step 9 — Add Job in Rwanda fixtures and parser tests

- **Exact goal:** Characterize JobInRwandaScanner listing/detail/JSON-LD/fallback parsing without live requests or delays.
- **Files likely to be created or modified:** Create `tests/scanners/test_job_in_rwanda.py` and sanitized files under `tests/fixtures/job_in_rwanda/`.
- **Commands to run:** `python -m pytest tests/scanners/test_job_in_rwanda.py -q`.
- **Validation:** Fake URL mapping supplies every soup; deduplication and extracted fields match current behavior; any unexpected network call fails.
- **Rollback approach:** Remove the test and its fixture directory.

## Step 10 — Validate automation configuration statically

- **Exact goal:** Parse and assert the two GitHub Actions workflow contracts without executing them.
- **Files likely to be created or modified:** Create `tests/workflows/test_github_actions.py`.
- **Commands to run:** `python -m pytest tests/workflows/test_github_actions.py -q`; optionally `actionlint` only after separate approval and installation.
- **Validation:** YAML parses and required commands, concurrency, scheduler disabling, and secret references remain intact.
- **Rollback approach:** Remove the workflow test; workflow files remain unchanged.

## Step 11 — Establish the quality baseline

- **Exact goal:** Run the complete safe suite, capture coverage, and gate new tests with Ruff.
- **Files likely to be created or modified:** Potentially adjust `pytest.ini`, create a minimal Ruff configuration only if defaults are unsuitable, and add a non-production CI test workflow in a separately reviewed task.
- **Commands to run:** `python -m pytest -q`; coverage and Ruff commands from Quality Gates.
- **Validation:** Default suite passes offline; coverage report is archived; Ruff passes for tests; application lint findings are documented rather than bulk-fixed.
- **Rollback approach:** Revert only quality configuration/CI additions; retain useful tests unless a specific test is invalid.

## Step 12 — Expand mutation-route characterization

- **Exact goal:** Cover status, notes, pipeline add/remove/update, validation failures, redirects, and a safely mocked `/scan`.
- **Files likely to be created or modified:** Create `tests/routes/test_opportunity_mutations.py`.
- **Commands to run:** `python -m pytest tests/routes/test_opportunity_mutations.py -q`.
- **Validation:** Current database transitions and 303/400/404 responses are explicit; `/scan` only calls a spy.
- **Rollback approach:** Remove this test file; no production code or persisted data changes.

# First Coding Task

After this plan is approved, the smallest first coding task should be:

> Create the isolated pytest foundation and four smoke/regression tests without modifying production code.

Exact scope:

- Create `requirements-dev.txt` with reviewed compatible ranges for `pytest`, `httpx`, `pytest-cov`, `ruff`, and `PyYAML`.
- Create minimal pytest configuration.
- Create `tests/conftest.py` that, before application imports:
  - forces a pytest-temporary SQLite `DATABASE_URL`;
  - disables the internal scheduler;
  - removes Brave keys;
  - provides a per-test SQLAlchemy session factory;
  - patches `app.main.SessionLocal`;
  - blocks live socket/HTTP connections.
- Create `tests/routes/test_startup.py` with one safe startup/root smoke test.
- Create `tests/routes/test_dashboard.py` with one empty-dashboard test.
- Create `tests/routes/test_pipeline.py` with one empty-pipeline test.
- Create `tests/unit/test_opportunity_classifier.py` with one explicit regression test proving the current qualifying classifier input raises the `raw`-argument `TypeError`.

Do not refactor `app.main`, do not fix the classifier in the same task, do not add scanner fixtures yet, and do not add CI yet.

Commands after dependency installation is explicitly approved:

```powershell
python -m pytest tests/routes/test_startup.py tests/routes/test_dashboard.py tests/routes/test_pipeline.py tests/unit/test_opportunity_classifier.py -q
python -m ruff check tests
python -m ruff format --check tests
git status --short
```

Success means the four tests pass offline, scheduler spies prove no scheduler startup, the known classifier defect is explicit, `opportunities.db` is untouched, and only test/development configuration files have changed.

# Risks and Open Questions

- **Import-time database creation:** `app.main` creates tables during import. The test environment must be established before any test module imports it.
- **Dependency override limitation:** Current routes do not use `get_db`; the first suite must patch imported session factories. Decide later whether a small dependency-injection refactor belongs at the end of Phase 0 or Phase 1.
- **Classifier contract defect:** Confirm that the current `TypeError` is reproducible in the approved first coding task. Fix it only in a separate change with before/after tests.
- **Potential ORM relationship failure:** Importing/using `Lead` or `Proposal` may trigger mapper errors because reverse relationships are absent. Phase 0 should not import them into ordinary route fixtures until a focused characterization is designed.
- **SQLite schema drift:** Local databases may have columns added by runtime DDL that differ from fresh model-created databases. Do not inspect or migrate production; later use sanitized schema inventories.
- **TestClient threading:** Use file-backed SQLite and `check_same_thread=False`; avoid ordinary in-memory SQLite.
- **Global imported session factories:** Each module must be patched at its lookup location. Missing one could write to the session factory created at import.
- **Environment loading:** `load_dotenv()` in discovery modules can load repository values. Avoid importing those modules in the first task or clear/override the environment before import.
- **Network blocking coverage:** The blocker must cover Requests, urllib/robots, sockets, and future clients without breaking in-process ASGI tests.
- **Workflow YAML parsing:** PyYAML's YAML 1.1 behavior can interpret `on` as a boolean. Use a safe loader/configuration appropriate for GitHub Actions.
- **GitHub workflow semantics:** YAML parsing alone does not verify the full Actions schema. Decide whether to pin `actionlint` after the basic suite.
- **Coverage policy:** Capture a baseline before choosing thresholds. A high immediate threshold would incentivize shallow tests or block unrelated work.
- **Ruff baseline:** Current application formatting/lint results are unknown. Gate new tests first; do not create a repository-wide formatting diff.
- **Time-dependent tests:** Current code directly calls `date.today()`, `datetime.utcnow()`, and `datetime.now()`. Prefer stable relative dates and bounded assertions before adding a time-freezing dependency.
- **HTML fixture provenance:** Confirm that each fixture is minimal, sanitized, legally retainable, and contains no personal or secret data.
- **Missing `app.discover_sources`:** The internal job path references a module that is not tracked. Characterize this configuration mismatch; do not create the module in Phase 0 planning.
- **Current workflow exposure:** Existing GitHub schedules reference `DATABASE_URL` and therefore may access production when run. Phase 0 tests must inspect these files only; they must never dispatch workflows.
- **PostgreSQL parity:** Determine when a disposable PostgreSQL CI service is permitted. SQLite cannot validate the production dialect.
- **Application startup definition:** Decide whether "startup" means import plus lifespan entry or also a health endpoint. No dedicated health endpoint currently exists.
- **HTML assertion stability:** Prefer status, semantic content, and persisted state over full template snapshots.

# Acceptance Criteria

Phase 0 is complete when all of the following are true:

1. A default test command runs entirely offline and passes on a clean checkout with development dependencies installed.
2. Test collection and application import cannot access production PostgreSQL or the real local `opportunities.db`.
3. The internal scheduler, scanner entry point, Brave Search, subprocess automation, SMTP, and live HTTP are disabled or blocked during tests.
4. Application startup and the dashboard, detail, and pipeline routes have characterization tests.
5. Dashboard filters, counts, 404 behavior, detail `Seen` transition, and pipeline selection are covered.
6. Filtering helpers, procurement evidence, rejection behavior, and a small hand-verifiable scoring set are covered.
7. The current `FilteredOpportunity`/classifier contract failure has an explicit regression test and is not silently hidden.
8. URL normalization behavior is characterized without merging the existing implementations.
9. SQLite model persistence, unique URL behavior, duplicate opportunity updates, and source health persistence are tested in disposable databases.
10. Company website and Job in Rwanda parsing use reviewed, sanitized local HTML fixtures.
11. A test proves an accidental live network request fails.
12. Both GitHub Actions workflow files receive YAML and repository-specific structural validation without being executed.
13. Coverage is measured and recorded; no arbitrary blocking threshold is imposed before the baseline exists.
14. Ruff gates new test code; application-wide findings are recorded and addressed incrementally.
15. PostgreSQL-only gaps are explicitly documented for Phase 1 integration testing.
16. No production secrets, services, databases, workflows, or Render settings are used or changed.
17. No architectural refactor, tenant model, database migration, or unrelated application rewrite is included in Phase 0.
18. Every Phase 0 change remains small, reviewable, independently testable, and reversible.
