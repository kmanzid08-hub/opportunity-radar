# Phase 1 Architecture Audit and Migration Readiness

## 1. Executive Summary

Opportunity Radar is currently a server-rendered FastAPI application with a
small SQLAlchemy persistence layer, synchronous scanners, two partially
overlapping discovery implementations, and several competing automation
paths. Its useful characterization suite now protects startup, routes,
filtering, scoring, source health, URL handling, persistence, and local HTML
parsing. That baseline makes an incremental migration possible.

The current architecture is not tenant-safe. There is no user,
authentication, organization, membership, or tenant-context model. Every
route and job can read or mutate all rows. Public opportunity facts,
organization-specific relevance, and private pipeline state are stored
together in `opportunities`. A unique `source_url` makes an opportunity
globally deduplicated, but its score, status, assignee, and notes are also
global, so a second organization could neither maintain independent state nor
be isolated.

Database lifecycle is the first technical blocker. `app.database` constructs
the engine and `SessionLocal` at import. `app.main` calls
`Base.metadata.create_all()` at import, `app.scanner.ensure_sqlite_schema()`
performs ad-hoc SQLite DDL, discovery and seed commands create tables, and four
hand-written migration scripts bypass a migration history. These paths must
be replaced by an Alembic baseline before tenant tables or data splitting.

The safest Phase 1 direction is a modular monolith, not distributed
microservices. One codebase should have explicit domain boundaries, one
configuration system, one session dependency, one migration history, and
separate web and worker processes. PostgreSQL should eventually enforce
tenant safety in addition to application filters. SQLite should remain
supported for local development and fast tests, while PostgreSQL integration
tests prove constraints, locking, JSON/enum behavior, and optional row-level
security.

No schema or production change should begin until the current production
schema is inspected through an approved, non-production copy or schema-only
dump. The tracked model definitions and historical DDL scripts do not prove
that every deployed database has the same shape.

## 2. Current Architecture Map

### 2.1 Runtime flow

```text
Browser
  -> app.main FastAPI routes
  -> directly imported SessionLocal
  -> Opportunity / Source tables
  -> Jinja templates

POST /scan or external schedule
  -> app.scanner.run_scanner
  -> JobInRwandaScanner + CompanyWebsiteScanner
  -> RawOpportunity
  -> app.filters.classify_opportunity
  -> FilteredOpportunity (currently contract-inconsistent)
  -> app.scanner.save_opportunity

GitHub Website Discovery workflow
  -> app.scheduled_discovery
  -> automation_job_state raw SQL gate
  -> app.source_discovery.RwandaSourceDiscovery
  -> Source table
```

### 2.2 Package assessment

| Current path | Current responsibility | Architectural issue |
|---|---|---|
| `app/main.py` | App creation, routes, queries, mutations, templates, scheduler lifecycle, manual scan | Web, domain, persistence, and operations are coupled. |
| `app/database.py` | URL parsing, engine, session factory, dependency | Import-time global state prevents clean configuration and injection. |
| `app/models.py` | `Opportunity`, `Source`, workflow constants | Global facts and tenant workflow are mixed. |
| `app/lead_models.py` | Dormant normalized `Lead` model | Conflicts with flattened lead columns and has an unresolved `back_populates`. |
| `app/proposal_models.py` | Dormant `Proposal` model | Not integrated into routes; unresolved `back_populates`; hard-coded RWF. |
| `app/scanner.py` | Schema repair, scan orchestration, classification, persistence | Runtime DDL, business logic, and transactions are coupled. |
| `app/scanners/` | Source-specific fetching and parsing | Company scanner also queries and updates database health directly. |
| `app/filters.py` | Hard-coded relevance categories and scoring | One global Rwanda/professional-services profile; output contract defect. |
| `app/source_discovery.py` | Active Brave discovery and source persistence | Rwanda- and organization-specific rules, HTTP, scoring, DDL, and DB writes in one class. |
| `app/discovery/` | Second Brave discovery/classification/repository path | Duplicates active discovery concepts and uses a different API-key name. |
| `app/source_quality.py` | Source scoring and Rwanda trust rules | Useful pure logic, but country policy is hard-coded. |
| `app/source_health.py` | Source health mutation and scoring | Direct global sessions and duplicated scoring formula. |
| `app/internal_scheduler.py` | In-web file-backed scheduler | Web process owns thread, lock, state file, and log file. |
| `app/automation.py` | Standalone file-backed scheduler | Overlaps internal scheduler with incompatible state shape. |
| `app/jobs.py` | Job wrappers | Website job references nonexistent `app.discover_sources`. |
| `app/scheduled_discovery.py` | Database-backed discovery gate | Creates an unmodeled table using PostgreSQL-oriented raw SQL. |
| `app/crawler/` | Empty placeholders | No current behavior; should not drive Phase 1 design. |
| `app/api.py` | Empty placeholder | No API boundary currently exists. |

### 2.3 Active deployment shape

- `render.yaml` declares one PostgreSQL database and one FastAPI web service.
- The Render web service explicitly sets `ENABLE_INTERNAL_SCHEDULER=false`.
- `.github/workflows/daily-tender-scan.yml` runs `python -m app.scanner`.
- `.github/workflows/website-discovery.yml` runs
  `python -m app.scheduled_discovery`.
- `DEPLOYMENT.md` claims Render creates two cron jobs, but `render.yaml`
  contains none; that documentation is stale.
- `vercel.json` is only a proxy placeholder and has no application logic.

## 3. Database Audit

### 3.1 Initialization, sessions, and DDL

| File and symbol | Current behavior | Risk | Phase 1 change | Order |
|---|---|---|---|---:|
| `app/database.py:DATABASE_URL` | Reads environment at import; defaults to `sqlite:///./opportunities.db`; rewrites PostgreSQL schemes. | A missing production variable silently selects a local file; configuration is frozen at import. | Read validated typed settings; fail safely in non-local environments. | 1 |
| `app/database.py:engine` | Calls `create_engine()` at import. | Import creates global infrastructure and can create a SQLite file on first connection. | Add an engine factory and app/worker initialization boundary. | 1 |
| `app/database.py:SessionLocal` | Global sessionmaker imported throughout the codebase. | Monkeypatching must target every importer; tenant policy cannot be centralized. | Expose a session factory through `app.db.session`; inject `Session` into services/routes. | 5 |
| `app/database.py:get_db` | Yields and closes a session but is unused by routes. | Existing dependency gives a false impression of DI coverage. | Make it the web boundary and derive tenant-aware unit-of-work helpers from it. | 5 |
| `app/main.py` module body | Calls `Base.metadata.create_all(bind=engine)` during import. | Import mutates schema and can touch/create the configured database. It bypasses migration review. | Remove only after Alembic baseline and startup tests prove schema checks. | 4 |
| `app/main.py` helpers/routes | Ten direct `with SessionLocal()` blocks and explicit commits. | No shared transaction policy, rollback mapping, or tenant filter. | Inject sessions; call tenant-aware services/repositories. | 5, 9 |
| `app/scanner.py:ensure_sqlite_schema` | Calls `create_all`; introspects SQLite; runs `ALTER TABLE` for missing source columns. | Runtime code silently changes local schema; schema differs by dialect and execution history. | Represent all changes in Alembic; replace with a read-only schema/version preflight. | 2–4 |
| `app/scanner.py:approve_existing_sources` | Opens and commits a global session; also changes approval/defaults. | A scan has hidden global data-cleanup side effects. | Move backfills to migration/data command and source policy to service. | 4, 11 |
| `app/scanner.py:save_opportunity` | Own transaction per item; deduplicates by exact `source_url`; mixes facts and tenant workflow. | Partial scan commits, weak canonical identity, no tenant context, race on unique insert. | Use ingestion service, canonical key, upsert/retry, then tenant qualification service. | 8 |
| `app/scanner.py:mark_expired_opportunities` | Globally mutates status based on deadline. | Expiry fact and tenant workflow status are coupled. | Update global derived expiry separately; never overwrite tenant workflow automatically. | 8 |
| `app/scanners/company_websites.py` | `_get_sources` and four health methods open direct sessions. | Parser/HTTP worker owns persistence and global source policy. | Pass source DTOs into scanner; persist run/health through source services. | 5, 11 |
| `app/source_health.py` | Four methods create and commit sessions. | Duplicate transaction boundaries and source-scoring implementation. | Accept an injected session/repository; retain pure calculator. | 5 |
| `app/discovery/source_repository.py:save_discovered_source` | Direct session and commit; exact normalized monitor URL uniqueness. | Global writes and race-prone check-then-insert. | Global source repository with database constraint/upsert and run provenance. | 5, 11 |
| `app/source_discovery.py:run` | Calls `Base.metadata.create_all()` when the discovery job runs. | Worker execution mutates schema outside Alembic. | Remove after baseline; worker should refuse an outdated schema. | 4 |
| `app/source_discovery.py:_save_candidate` | Direct global session/commit. | HTTP orchestration, scoring, and persistence share one transaction policy. | Split connector, classifier, discovery service, and repository. | 11 |
| `app/seed_sources.py:seed_sources` | Calls `create_all`, then inserts/updates Rwanda sources. | Seed command can create schema and embeds country-specific production data. | Convert to explicit country-pack import or administrative CLI after migrations. | 4, 12 |
| `app/scheduled_discovery.py` | Uses `engine.begin()` and creates/queries/upserts `automation_job_state`. | Unmodeled schema, PostgreSQL `ON CONFLICT`, and job locking assumptions bypass migrations. | Model migration-owned `DiscoveryRun`/job lease or use queue backend. | 2, 11 |
| `app/migrate_phase2.py` | Raw `ALTER TABLE` and data updates. | No revision graph, dialect-specific DDL, non-repeatable operational history. | Freeze as historical reference; supersede with Alembic baseline. | 2 |
| `app/migrate_opportunity_leads.py` | Adds flattened lead columns via raw DDL. | Conflicts with `Lead` table design. | Decide canonical target before baseline follow-up revisions. | 2, 8 |
| `app/migrate_leads.py` | May drop an empty `proposals` table and calls targeted `create_all`. | Destructive, model-registration dependent, and outside a revision history. | Never run in Phase 1; document observed deployed state. | 2 |
| `app/migrate_proposals.py` | Calls targeted `create_all` for `Proposal`. | Relationship is inconsistent and deployment presence is unknown. | Baseline actual state; redesign as tenant-owned proposal later. | 2, 8 |
| `tests/conftest.py` | Creates in-memory SQLite engine and tables. | Safe for tests, but imports only current `app.models`; does not prove dormant tables or PostgreSQL behavior. | Retain fast fixture; add migration-based SQLite and PostgreSQL suites. | 2–3 |

### 3.2 Dialect assumptions

SQLite assumptions:

- Default relative file URL and `check_same_thread=False` in
  `app/database.py`.
- `DATETIME`, numeric boolean defaults, inspector-driven `ALTER TABLE`, and
  missing-column repair in `app/scanner.py`.
- Historical migration scripts use SQLite-friendly raw DDL.
- Tests use `StaticPool` with one in-memory connection.

PostgreSQL assumptions:

- Render provisions PostgreSQL and workflows receive its URL.
- `app/scheduled_discovery.py` uses `TIMESTAMP WITH TIME ZONE` and
  `ON CONFLICT`.
- `psycopg[binary]` is installed and URL schemes are rewritten.

SQLite cannot prove PostgreSQL locking, concurrent unique inserts, timezone
round-trips, numeric precision, constraint deferral, transaction isolation,
index plans, JSON behavior, or row-level security. Add PostgreSQL integration
tests before tenant enforcement or worker concurrency is enabled.

### 3.3 Transaction and import risks

- Most functions commit internally, preventing a caller from atomically
  combining operations.
- Exception paths generally rely on context-manager close rather than an
  explicit service-level rollback/error contract.
- `save_opportunity()` commits per listing, so a failed scan is partially
  persisted with no `ScanRun` record identifying completeness.
- Importing `app.main` imports `app.database`, models, schedulers, scanners,
  and then performs schema creation.
- Importing `app.source_discovery` or
  `app.discovery.search_discovery` calls `load_dotenv()`, allowing repository
  environment files to affect behavior outside one configuration boundary.

## 4. Model Inventory

### 4.1 Current declared models

| Model | Table / key | Important columns and constraints | Relationships | Ownership decision | Backfill/migration risk |
|---|---|---|---|---|---|
| `app.models.Opportunity` | `opportunities`; integer `id` | `source_url` unique; issuer text, title, category, description, deadline, source text, score/reason, status, expiry, notes, timestamps, and flattened lead fields | None declared | Split into global `Opportunity` and tenant-owned `OrganizationOpportunity`; later tenant proposal/task records | Highest risk: every row mixes both layers. Preserve IDs through a mapping table; backfill one default organization and one tenant-state row per opportunity. |
| `app.models.Source` | `sources`; integer `id` | `monitor_url` unique; base URL, domain, type, discovery provenance, confidence, approval/active flags, scan health/cadence | None | Split global `Source` identity/health from tenant-owned `OrganizationSource` preferences | Clarify whether existing approval/active/cadence are platform or UT CPA policy before moving them. |
| `app.lead_models.Lead` | `leads`; integer `id`; unique FK `opportunity_id` | Status, assignee text, priority, next action/dates, notes, timestamps | `opportunity` with `back_populates="lead"` | Replace or evolve into tenant-owned workflow/pursuit entity linked to `OrganizationOpportunity` | `Opportunity.lead` is absent, so mapper configuration is inconsistent. Table existence/data is unknown. Flattened lead columns currently drive the UI. |
| `app.proposal_models.Proposal` | `proposals`; integer `id`; unique FK `opportunity_id` | Bid state, manager text, value, hard-coded `RWF`, effort/security/dates/result/notes | `opportunity` with `back_populates="proposal"` | Tenant-owned proposal linked to an organization opportunity; allow multiple versions/submissions if required | `Opportunity.proposal` is absent; table is not used by routes; historical lead migration may drop it if empty. |

Only `Opportunity` and `Source` are imported by the normal web model path.
`Lead` and `Proposal` are imported by standalone scripts, and their reverse
relationship attributes are missing from `Opportunity`. Alembic model
discovery must not blindly baseline all four declarations without first
reconciling this difference against an approved schema snapshot.

`automation_job_state` is an unmodeled table created by
`app.scheduled_discovery`; include its actual presence in the baseline audit.

### 4.2 Required Phase 1 models

| Target model | Purpose and key relationships | Ownership / isolation |
|---|---|---|
| `Organization` | Tenant identity, slug, lifecycle, locale, timezone, default currency; parent of settings and memberships | Tenant root; globally identifiable, administratively restricted |
| `User` | Authentication identity, verified email, status, last login | Global identity; no tenant data without membership |
| `OrganizationMember` | Join between user and organization with role/status | Tenant-owned; unique `(organization_id, user_id)` |
| `Opportunity` | Canonical public notice facts, issuer, country, source, normalized URL/external ID, content, dates | Global shared catalog |
| `OrganizationOpportunity` | Qualification, score, explanation, status, assignee, notes, visibility, decision, deadline override | Tenant-owned; unique `(organization_id, opportunity_id)` |
| `Source` | Canonical endpoint/domain/type and platform-level technical health | Global where public; private connector credentials must not live here |
| `OrganizationSource` | Tenant enablement, approval, cadence override, profile, labels | Tenant-owned; unique `(organization_id, source_id)` |
| `ScanRun` | Scanner execution, source, timestamps, status, counts, error, lease/idempotency key | Platform operational; optionally linked to requesting organization/profile |
| `DiscoveryRun` | Search-provider run, country/profile/query set, counts, cost/error, idempotency | Platform or tenant-owned depending on initiator; explicit visibility |
| `ScoringProfile` | Versioned tenant rules, services, keywords, exclusions, weights, threshold | Tenant-owned; immutable versions referenced by qualification results |
| `WorkflowStatus` | Configurable organization workflow states and ordering | Tenant-owned; system defaults copied at onboarding |
| `AuditLog` | Actor, organization, action, resource, before/after metadata, request/run correlation | Tenant-owned and append-only; platform security events separately scoped |

Supporting early models should include `Role`, `Permission`,
`MemberRole`, `Country`, `Currency`, `Language`, `OpportunityType`,
`Issuer`, and normalized `ExternalIdentifier`. Later pursuit models
(`Proposal`, `Task`, `Attachment`, `Tag`, `Activity`) should reference
`OrganizationOpportunity`, never the global opportunity alone.

## 5. Multi-Tenancy Audit

### 5.1 Tenant context

Every web request must resolve:

1. authenticated `User`;
2. selected `Organization` from a trusted server-side membership lookup;
3. active `OrganizationMember` and permissions;
4. a tenant context passed to services and repositories.

Do not trust `organization_id` from a form, query parameter, or URL without
membership authorization. Browser routes may use an organization slug in the
URL or a signed session selection, but repository filtering must use the
resolved context. Workers must carry an explicit organization/profile ID in
their job payload when processing private configuration.

### 5.2 Affected locations

| Location | Required context and filter | Data classification | Leakage risk |
|---|---|---|---|
| `app.main:home`, `get_status_counts`, `get_categories` | Organization from authenticated request; join global opportunities through `OrganizationOpportunity.organization_id` | Shared facts plus tenant state | Critical: currently returns all rows and global counts |
| `app.main:opportunity_detail` | Load global opportunity only through authorized tenant join | Shared facts plus private notes/workflow | Critical: direct numeric ID enables cross-tenant reads |
| `update_opportunity`, `quick_update_status` | Mutate tenant join row after membership/permission check | Tenant-private | Critical: direct global mutation |
| `add_to_pipeline`, `remove_from_pipeline`, `update_pipeline_opportunity` | Mutate tenant pursuit state; assignee must be a member ID | Tenant-private | Critical: notes, assignments, decisions leak |
| `pipeline_page` | Filter by organization and tenant workflow state | Tenant-private | Critical |
| `scan_internet` | Require privileged platform/tenant permission; enqueue, never execute inline | Operational | High: any visitor currently triggers external work |
| `app.scanner.save_opportunity` | Upsert global facts, then create/update qualifications per scoring profile | Mixed | Critical: score/reason/status currently overwritten globally |
| `app.filters.classify_opportunity` | Accept versioned `ScoringProfile` and country/language rules | Tenant configuration | High: one firm’s keywords currently define relevance for all |
| `CompanyWebsiteScanner._get_sources` | Platform sources plus enabled `OrganizationSource` policy, or platform-only scanning | Mixed | High: approval and cadence semantics are global |
| `SourceHealthManager` and scanner health methods | Platform technical health; tenant preferences filtered separately | Global operational | Medium if preferences remain mixed |
| `source_repository.save_discovered_source` | Global source upsert; tenant attribution in discovery run/link | Mixed | High if private sources or searches become globally visible |
| `RwandaSourceDiscovery` / `run_search_discovery` | Explicit country/search profile and optional organization | Mixed | High: search intent and private connectors can leak |
| Jobs and schedulers | Explicit run scope and idempotency key | Operational | High: unscoped jobs may process all tenant configuration |
| `seed_sources.py` | Country pack and platform-admin command | Global catalog | Medium |
| All templates and forms | Organization branding/context; opaque authorized resource URLs; CSRF protection | Mixed | Critical without server-side enforcement |

Application filtering is mandatory, but PostgreSQL defense in depth should be
evaluated once the schema stabilizes. Row-level security is appropriate for
tenant-owned tables if connection/session context can be set reliably.
Regardless of RLS, composite unique constraints and foreign keys should carry
`organization_id` where needed to make cross-tenant references impossible.

## 6. Global Versus Tenant Data Design

### 6.1 Global public opportunity

Move or retain globally:

- canonical title;
- issuer/organization name, later `issuer_id`;
- description and source content hash;
- canonical source URL and normalized URL;
- source and connector identity;
- external notice/reference ID;
- published, discovered, last-seen, updated, and deadline dates;
- country/region and source language;
- opportunity type;
- platform-derived expiration/withdrawal state;
- raw document metadata and provenance.

`category` must be split conceptually. A neutral global opportunity type or
taxonomy tag may be global, while “Audit”, “Tax”, or another service match is
tenant-specific. Global deduplication should use a prioritized identity:
`(source_id, external_id)`, then canonical URL, then a reviewed fingerprint.
Aliases should preserve all observed URLs.

### 6.2 Organization-specific opportunity state

Move to `OrganizationOpportunity` or related tenant tables:

- `match_score` and `match_reason`;
- matched services/categories/keywords and scoring-profile version;
- `status`, `user_notes`, `is_lead`;
- `assigned_to` as `assigned_user_id` or membership ID;
- `lead_status`, `lead_priority`;
- `next_action`, due date, follow-up date, and lead notes;
- hidden, archived, pinned, watched, and notification state;
- organization deadline override;
- bid/no-bid decision and reasons;
- tenant tags, attachments, activities, tasks, and proposals.

Current `is_expired` should become a global derived fact based on the public
deadline. A tenant workflow status must not be changed to `Expired`
automatically; a tenant may still pursue a clarification or late submission.

### 6.3 Safe transition

1. Create a default organization representing the current installation.
2. Create one tenant-state row for every current opportunity.
3. Copy score, reason, status, notes, and flattened pipeline fields.
4. Keep current opportunity IDs stable while routes temporarily read through
   a compatibility service.
5. Add dual-read verification before switching templates.
6. Stop writes to old workflow columns, verify parity, then drop them only in
   a later release.

## 7. Startup Side-Effect Audit

| Side effect | Location | Destination |
|---|---|---|
| Environment read and URL rewriting | `app/database.py` import | Typed `app.core.config` settings |
| Engine/session construction | `app/database.py` import | Explicit DB infrastructure initialization/factory |
| Schema creation and implicit SQLite file creation | `app/main.py` import | Alembic deployment/release command |
| Static/template directory initialization | `app/main.py` import | App factory; safe after paths are validated |
| Scheduler singleton import | `app.main` -> `app.internal_scheduler` | Remove from web dependencies |
| Scheduler thread, lock file, state file, and log file | `app.main:lifespan` / scheduler start | Dedicated worker plus external scheduler |
| Network scan inside HTTP request | `app.main:scan_internet` | Authorized enqueue endpoint / admin CLI |
| Runtime schema repair | `app.scanner.run_scanner` | Alembic; scanner performs version preflight only |
| Source approval/data repair | `app.scanner.approve_existing_sources` | Migration/backfill or explicit admin service |
| `.env` loading | `app.source_discovery`, `app.discovery.search_discovery` imports | One application entry-point configuration load |
| Table creation during discovery/seed | `RwandaSourceDiscovery.run`, `seed_sources` | Alembic plus explicit CLI |
| Automation table creation | `app.scheduled_discovery._ensure_state_table` | Alembic-owned run/lease tables |
| HTTP session construction | Scanner/discovery constructors | Connector factory in worker; construction is safe, network use remains explicit |
| Logging file configuration | `InternalScheduler._configure_logging` | Process logging configuration/stdout |

FastAPI lifespan should manage only web-process resources such as connection
pool disposal and possibly client lifecycles. It should not start scans,
workers, or schema changes.

## 8. Scheduler and Worker Audit

| Path | Trigger/cadence/entry | Status and database dependency | Overlap, failure, idempotency | Destination |
|---|---|---|---|---|
| Daily GitHub workflow | Cron `0 4 * * *`, Africa/Kigali; `python -m app.scanner` | Production-active by repository configuration; PostgreSQL secret | Workflow concurrency prevents same-workflow overlap, but no durable `ScanRun`; per-row commits yield partial runs | External scheduler enqueues one daily scan job |
| Website GitHub workflow | Cron `20 4 */5 * *`; `python -m app.scheduled_discovery` | Production-active; PostgreSQL state table and Brave key | Calendar `*/5` is not exact 120 hours; DB gate prevents early execution; no lease during long run | External scheduler enqueues discovery with durable idempotency key |
| `scheduled_discovery.py` | Invoked by workflow; internal five-day interval | Active gate | Raw state DDL, no running lease, success recorded only after completion | Worker run/lease service |
| `internal_scheduler.py` | Web lifespan; checks every 60 seconds; tender daily, discovery five days | Disabled in Render and `.env.example`, enabled by code default elsewhere | Duplicates external schedules; local file lock only; nested JSON state; website command is broken | Remove from web; optional development scheduler calls same queue |
| `automation.py` | Manual standalone command; daily/five-day | No tracked trigger; file state | Overlaps internal scheduler; uses the same filename with incompatible flat JSON; subprocess return-code handling only | Retire after worker CLI exists |
| `jobs.py:run_tender_scan` | Internal scheduler | Local/disabled in production | Direct synchronous call | Worker task |
| `jobs.py:run_website_discovery` | Internal scheduler | Broken path: `app.discover_sources` is absent | Always fails if invoked | Remove/replace with canonical task |
| Company source due logic | Daily scanner calls `_source_is_due`; model default 12h, scanner fallback 24h | Active inside tender scan | Cadence defaults conflict; health updated directly; no per-source lease | Worker fan-out by source with `ScanRun`/attempts |
| `POST /scan` | Any HTTP caller; immediate | Active web route | No authentication, blocks web worker, duplicates scheduled scan | Authorized enqueue-only endpoint |
| Render | Web service and PostgreSQL only | No Render cron/worker declared | Documentation incorrectly says two cron jobs | Add worker service only when queue choice is approved |

Target responsibility split:

- **Web:** authenticate, authorize, query tenant-safe services, enqueue
  commands, display run status.
- **Worker:** execute discovery/scanning/parsing/scoring/persistence; use leases,
  retries, timeouts, and run records.
- **External scheduler:** emit idempotent recurring commands; own cadence, not
  business logic.

Use at-least-once delivery assumptions. Each job needs an idempotency key,
durable status, attempt count, lease expiry, error classification, and
correlation ID. Database uniqueness must make repeated ingestion safe.

## 9. Configuration Audit

### 9.1 Environment variables

| Value | Current readers | Classification | Target |
|---|---|---|---|
| `DATABASE_URL` | `app.database`, workflows, Render | Secret/environment-specific | Typed settings and secret manager; required outside local/test |
| `BRAVE_API_KEY` | `app.source_discovery`, workflows, Render | Secret/provider credential | Worker connector secret |
| `BRAVE_SEARCH_API_KEY` | `app.discovery.search_discovery` | Secret/provider credential | Consolidate with canonical connector configuration |
| `ENABLE_INTERNAL_SCHEDULER` | `app.main`, workflows, Render, tests | Operational runtime | Remove after web/worker separation |
| `TENDER_SCAN_COMMAND` | `app.automation`, tests | Operational/legacy | Retire with automation path |
| `SOURCE_DISCOVERY_COMMAND` | `app.automation`, tests | Operational/legacy | Retire with automation path |
| `PYTHON_VERSION` | `render.yaml` | Environment/deployment | Deployment configuration |
| `PORT` | Render start command | Environment/deployment | Platform-provided runtime value |

### 9.2 Hard-coded policy

- Country-specific: Rwanda query lists, `.rw/.gov.rw/.ac.rw` boosts,
  `JobInRwandaScanner` URLs/patterns, Rwanda seed sources, Africa/Kigali
  workflow timezone.
- Organization-specific: “UT CPA Ltd” template text and HTTP user agents;
  professional-services filter categories.
- Organization workflow: opportunity, lead, priority, proposal, and result
  tuples in model modules.
- Currency: proposal default `RWF`.
- Scoring: all terms, weights, penalties, thresholds, and source trust rules
  in `filters.py`, `source_classifier.py`, and `source_quality.py`.
- Operations: request timeouts, page/source/query limits, crawl depth, delays,
  failure threshold, scan intervals, discovery interval, retry interval.
- Source policy: blocked domains, allowed document types, link terms,
  approval defaults, and auto-disable behavior.

Move application-wide validated primitives into typed settings. Keep secrets
only in environment/secret management. Put connector defaults in worker
configuration, country rules and source adapters in versioned country packs,
and services/keywords/exclusions/scoring/workflows/notifications/branding in
tenant-owned database configuration. Store each scoring result with the rule
version that produced it.

## 10. Route and Template Audit

### 10.1 Route inventory

| Method/path and handler | Current DB/mutation | Tenant requirement | Phase 1 risk |
|---|---|---|---|
| `GET /` — `home` | Global opportunity search/count; helper sessions | Organization join and tenant-state filters | Critical leakage; multiple sessions can observe inconsistent snapshots |
| `GET /opportunities/{id}` — `opportunity_detail` | Global `db.get`; changes `New` to `Seen` on read | Authorized organization opportunity; view activity separate from GET | Critical IDOR and unsafe read-side mutation |
| `POST /opportunities/{id}/update` | Global status/notes commit | Tenant row and edit permission | Critical cross-tenant write; no CSRF/authentication |
| `POST /opportunities/{id}/status` | Global status commit | Tenant row and edit permission | Critical cross-tenant write |
| `POST /opportunities/{id}/pipeline/add` | Sets flattened global lead fields | Tenant pursuit row | Critical cross-tenant write |
| `POST /opportunities/{id}/pipeline/remove` | Clears global `is_lead` only | Tenant pursuit row and defined archive/delete semantics | Critical; leaves other lead data populated |
| `GET /pipeline` — `pipeline_page` | All rows with `is_lead=true` | Organization filter | Critical private pipeline leakage |
| `POST /opportunities/{id}/pipeline/update` | Global assignee/status/actions/notes | Tenant row; assignee membership validation | Critical notes/assignment leakage |
| `POST /scan` — `scan_internet` | Runs network/database job synchronously | Platform-admin permission and enqueue | Critical operational abuse/availability risk |

There is no authentication, authorization, CSRF protection, rate limiting,
API versioning, or organization selector. `app/api.py` is empty.

### 10.2 Template assumptions

- `app/templates/index.html` hard-codes “for UT CPA Ltd”, assumes one global
  opportunity list and one global workflow status, links by raw integer ID,
  and exposes a scan form to every visitor.
- `app/templates/detail.html` hard-codes “UT CPA Ltd Review”, displays and
  edits global notes/pipeline state, and posts raw opportunity IDs without
  CSRF protection or tenant context.
- `app/templates/pipeline.html` assumes one global pipeline and stores
  assignees as free text rather than users/members.
- All templates assume English and server timezone/date formatting.
- There is no organization branding, locale, navigation context, role-aware
  control visibility, or tenant-safe URL namespace.
- `index.html` references summary variables such as `pipeline_count`,
  `high_priority_count`, `follow_up_today`, `overdue_followups`, and
  `closing_this_week` that `home()` does not currently provide. Undefined
  Jinja values can hide this contract drift.

Introduce tenant-aware view models rather than passing ORM instances directly
to templates. Keep authorization server-side; hiding a control is not access
control.

## 11. Proposed Modular-Monolith Boundaries

```text
app/
  core/             settings, security primitives, logging, clocks, errors
  db/               Base, engine/session factories, Alembic integration
  organizations/    organizations, memberships, roles, tenant context
  users/            identities, authentication, sessions
  opportunities/    global catalog, ingestion identity, tenant qualification
  sources/          source catalog, tenant source preferences, health
  scanning/         scanner ports, adapters, parsing, scan orchestration
  discovery/        search connectors, country/search profiles, source intake
  scoring/          versioned scoring profiles and qualification
  workflows/        tenant statuses, pursuits, proposals, tasks, activities
  jobs/             task definitions, leases, runs, retries, worker CLI
  web/              FastAPI app factory, dependencies, routes, view models
```

| Module | Owns | Public interface | Forbidden dependencies |
|---|---|---|---|
| `core` | Settings and cross-cutting primitives, no domain rows | `get_settings`, clock/ID/error interfaces | ORM models, FastAPI routes, scanners |
| `db` | Base, engine/session, migration metadata | session dependency/unit of work | Templates and business policy |
| `organizations` | Organization, membership, roles/permissions | resolve/authorize tenant context | Scanner implementations |
| `users` | User identity and authentication sessions | authenticate/current user | Opportunity persistence internals |
| `opportunities` | Global opportunity, issuer, tenant join, dedup aliases | ingest global facts; list/get/qualify for tenant | HTTP fetching, templates |
| `sources` | Source, organization source, health | register source; select due sources; record health | FastAPI and HTML parsing |
| `scanning` | Scanner protocol, source adapters, parsers | scan a source and return normalized candidates | Direct commits, tenant UI |
| `discovery` | Search connectors, queries/profiles, candidate source intake | execute discovery profile | Direct source-table mutation outside service |
| `scoring` | Profiles, versions, rule evaluator | score candidate for profile | HTTP/network and route objects |
| `workflows` | Statuses, pursuits, proposals, tasks, activity | tenant-authorized workflow commands | Global scanner internals |
| `jobs` | Scan/discovery runs, task payloads, leases/retries | enqueue/execute/status | Templates; implicit tenant |
| `web` | HTTP, dependencies, forms/view models/templates | app/router factory | Raw `SessionLocal`, scanner network calls |

Migration should proceed by extracting interfaces around existing code, not
by moving all files at once. New modules may temporarily call legacy pure
functions, but legacy modules must not import the new web layer.

## 12. Test-Impact Analysis

### 12.1 Tests that should largely remain

- Scanner HTML fixture tests should remain stable behind scanner adapters.
- Pure URL normalization and source-priority tests can move with modules
  without semantic changes.
- Filtering characterization should remain until versioned scoring parity is
  deliberately changed.
- The strict `FilteredOpportunity` xfail must remain until its repair is a
  separately approved compatibility task.

### 12.2 Tests needing changes

- `tests/conftest.py` must eventually construct typed test settings, use the
  new session dependency, run Alembic to head for migration tests, and provide
  organization/user/member fixtures.
- Route tests need authenticated clients and an active organization.
- Persistence tests must stop monkeypatching imported `SessionLocal` symbols
  once services accept sessions/unit-of-work explicitly.
- Source health persistence tests should target a source repository/service.
- Dashboard/pipeline expectations must use tenant view models and verify that
  global facts are combined with the correct tenant state.

### 12.3 Promotion and new suites

Promote route and persistence coverage into clear integration suites. Add:

- settings validation and “no silent production SQLite fallback” tests;
- Alembic clean-database upgrade-to-head and downgrade/upgrade smoke tests;
- migration tests from a sanitized copy of each known legacy schema shape;
- default-organization backfill count/checksum tests;
- two-organization isolation tests for every read and mutation route;
- authorization tests for member roles and cross-tenant numeric IDs;
- repository tests proving every tenant query requires organization context;
- global opportunity deduplication with two independent tenant-state rows;
- concurrent ingestion/upsert tests on PostgreSQL;
- source visibility and private-integration isolation tests;
- worker job idempotency, lease expiry, retries, partial failure, and resume;
- no scheduler/thread/schema mutation during web startup;
- configuration/profile version reproducibility tests;
- audit-log attribution and immutability tests;
- template contract tests for branding, locale, and role-aware controls.

Use a test assertion or architecture check that rejects `SessionLocal` imports
outside the DB infrastructure and approved worker composition roots after
centralization.

## 13. Staged Migration Plan

### Stage 1 — Introduce typed settings

- **Prerequisites:** Existing startup/network guards.
- **Files affected:** New `app/core/config.py`; `app/database.py`,
  `app/main.py`, `.env.example`, `requirements.txt`, focused tests.
- **Tests first:** Environment precedence, booleans, SQLite local default,
  PostgreSQL URL normalization, secret redaction, invalid production config.
- **Migration/backfill:** None.
- **Rollback:** Revert settings wiring; no data change.
- **Risk:** Import caching and `load_dotenv()` in discovery modules can
  override intended test/runtime behavior.

### Stage 2 — Introduce Alembic without changing schema

- **Prerequisites:** Stable settings/engine factory; approved schema snapshot
  from each environment class.
- **Files affected:** `alembic.ini`, `alembic/`, DB metadata imports,
  requirements, migration tests, deployment commands/documentation.
- **Tests first:** Empty SQLite/PostgreSQL migration smoke tests and metadata
  comparison.
- **Migration/backfill:** Create a reviewed baseline revision representing
  actual canonical schema, including a decision on dormant tables and
  `automation_job_state`.
- **Rollback:** Remove tooling before stamping any shared database. After
  stamping, restore schema/version table from backup if necessary.
- **Risk:** Tracked models may not equal production schema.

### Stage 3 — Baseline current schema

- **Prerequisites:** Schema diff reviewed; backups and restore rehearsal;
  explicit policy for already-existing databases.
- **Files affected:** Baseline revision, deployment docs/scripts, migration
  verification tests.
- **Tests first:** Upgrade empty DB, stamp matching legacy DB, reject
  mismatched DB, compare tables/columns/indexes/constraints.
- **Migration/backfill:** Normally schema-neutral stamp for matching deployed
  DBs; create schema only for new DBs.
- **Rollback:** Remove/stamp back only under documented operational procedure;
  never guess.
- **Risk:** Hand-written historical migrations created multiple legacy shapes.

### Stage 4 — Remove `create_all` and runtime DDL

- **Prerequisites:** Every supported environment is at Alembic head.
- **Files affected:** `app/main.py`, `app/scanner.py`,
  `app/source_discovery.py`, `app/seed_sources.py`,
  `app/scheduled_discovery.py`, legacy migration documentation.
- **Tests first:** Import/startup does not create files/tables; outdated schema
  fails clearly; scanner/discovery never executes DDL.
- **Migration/backfill:** Alembic revision for `automation_job_state` or its
  replacement.
- **Rollback:** Restore previous release only while keeping schema compatible.
- **Risk:** A forgotten local workflow may have depended on implicit creation.

### Stage 5 — Centralize session creation

- **Prerequisites:** App factory and settings boundary.
- **Files affected:** `app/database.py`, all `SessionLocal` importers, route
  dependencies, scanner/discovery/source repositories, tests.
- **Tests first:** Rollback-on-error, session closure, atomic service
  operations, no global importer monkeypatching.
- **Migration/backfill:** None.
- **Rollback:** Compatibility adapter can expose the old factory temporarily.
- **Risk:** Large blast radius; migrate one service boundary at a time.

### Stage 6 — Introduce organization tables

- **Prerequisites:** Central sessions and Alembic.
- **Files affected:** Organization/user/member domain modules, models,
  migration, admin/onboarding services, tests.
- **Tests first:** Unique slugs/memberships, role checks, disabled membership,
  organization lifecycle.
- **Migration/backfill:** Add nullable-independent organization, user, and
  membership tables without changing existing opportunity reads.
- **Rollback:** Drop only newly empty tables before production onboarding;
  otherwise retain and disable feature.
- **Risk:** Authentication design and user identity provider choice.

### Stage 7 — Backfill a default organization

- **Prerequisites:** Organization tables and approved identity for current
  owner.
- **Files affected:** Data migration/CLI, compatibility tenant resolver,
  deployment runbook, tests.
- **Tests first:** Idempotent backfill, deterministic organization ID/slug,
  row counts, rerun safety.
- **Migration/backfill:** Create default organization, initial admin/member,
  and organization settings without embedding UT CPA values in code.
- **Rollback:** Restore backup or remove only verified backfill-owned rows.
- **Risk:** Selecting the wrong production owner or locale/settings.

### Stage 8 — Split global and organization opportunity data

- **Prerequisites:** Default organization, complete model-field mapping,
  compatibility service.
- **Files affected:** Opportunity/workflow models and services,
  `app/scanner.py`, routes/templates, migrations, lead/proposal decisions.
- **Tests first:** Global dedup, tenant-state independence, parity of current
  dashboard/pipeline, dual-read comparison.
- **Migration/backfill:** Create `organization_opportunities`; copy all
  tenant fields; add constraints/indexes; preserve old columns temporarily.
- **Rollback:** Read old columns while retaining new table; do not drop old
  data in the same release.
- **Risk:** Category/expiry semantics and dormant Lead/Proposal data.

### Stage 9 — Add tenant filtering

- **Prerequisites:** Authenticated tenant context and backfilled join rows.
- **Files affected:** Every route/query/repository/template and worker payload.
- **Tests first:** Two-tenant positive/negative matrix for every operation,
  IDOR tests, membership changes.
- **Migration/backfill:** Add composite indexes/FKs; possibly nullable-to-not
  null transition after verification.
- **Rollback:** Feature flag to default organization only; never fall back to
  unfiltered queries.
- **Risk:** One missed query creates a critical breach.

### Stage 10 — Add database-level tenant protection

- **Prerequisites:** Application isolation suite green on PostgreSQL.
- **Files affected:** Alembic policies/constraints, session transaction
  context, PostgreSQL tests.
- **Tests first:** RLS or constraint enforcement with intentionally malicious
  cross-tenant operations and pooled connections.
- **Migration/backfill:** Add policies/composite constraints after all tenant
  IDs are valid.
- **Rollback:** Disable policies only under security-approved incident
  procedure; application filtering remains mandatory.
- **Risk:** Leaked session tenant context in connection pools.

### Stage 11 — Separate workers from web startup

- **Prerequisites:** Job/run tables, centralized services, idempotent ingestion.
- **Files affected:** `app/internal_scheduler.py`, `app/automation.py`,
  `app/jobs.py`, `app/scheduled_discovery.py`, `app/main.py`, workflows,
  Render config, new worker entry point.
- **Tests first:** No web background thread; enqueue authorization; retries,
  leases, duplicate delivery, crash recovery, run observability.
- **Migration/backfill:** Add run/attempt/lease tables.
- **Rollback:** External workflow may invoke the worker CLI synchronously
  while queue infrastructure is rolled back; do not re-enable web threads.
- **Risk:** Duplicate executions during cutover.

### Stage 12 — Move configurable rules out of code

- **Prerequisites:** Organization/country context and versioned profiles.
- **Files affected:** `filters.py`, source quality/classifier, discovery
  queries, scanner registrations, seed command, settings/admin UI.
- **Tests first:** Current default profile parity, profile versioning,
  country-pack selection, language and currency behavior.
- **Migration/backfill:** Seed neutral platform taxonomies and copy current
  rules into an explicitly named legacy/default profile for the default
  organization.
- **Rollback:** Pin organizations to the last known profile version.
- **Risk:** Silent scoring drift; always store evaluated profile version.

## 14. Risk Register

| Risk | Severity | Evidence | Mitigation |
|---|---|---|---|
| Cross-tenant data disclosure/mutation | Critical | No auth/context; all global queries and raw IDs | Tenant context, service filters, isolation matrix, PostgreSQL defense in depth |
| Production schema differs from tracked models | Critical | Hand-written DDL, dormant models, runtime repair | Approved schema-only inventory, backup, Alembic baseline validation |
| Global/tenant field split loses workflow data | Critical | All fields share `opportunities` | Additive table, idempotent backfill, counts/checksums, dual read, delayed drop |
| Import or startup mutates database | High | `create_all` in `app.main` | Baseline first, then remove and test no side effects |
| Duplicate/overlapping jobs | High | GitHub, internal scheduler, automation, manual route | One external scheduler, durable idempotency/leases, retire old paths |
| Broken local discovery job | High | `app.jobs` references absent module | Do not activate; replace through canonical worker task |
| Inconsistent automation state | High | Two file schemas use `automation_state.json` | Retire both file-backed schedulers |
| Partial ingestion and race conditions | High | Per-item commits and check-then-insert | Run records, atomic upserts, unique constraints, retries |
| Tenant context leaks through pooled DB connection | Critical | Future RLS/session variables | Transaction-scoped context reset and adversarial PostgreSQL tests |
| Scoring behavior changes invisibly | High | Hard-coded rules and known contract mismatch | Versioned profiles, parity fixtures, explicit migration |
| Private source/integration becomes global | High | One global Source table | Visibility classification and OrganizationSource/Integration ownership |
| Web route triggers expensive network scan | High | Unprotected `POST /scan` | Authenticated enqueue-only route, CSRF/rate limits |
| SQLite gives false confidence | Medium | Test suite is SQLite-only | PostgreSQL migration/concurrency/isolation CI |
| Stale deployment documentation | Medium | Render cron claims contradict `render.yaml`; old discovery cadence | Update docs alongside each operational change |
| Dormant Lead/Proposal mapper inconsistency | High | Missing reverse `back_populates` | Inventory actual tables/data; design canonical tenant pursuit model before import |
| Organization-specific hard-coding | Medium | UT CPA, Rwanda, RWF, English | Typed settings, tenant configuration, country packs, i18n |

## 15. Recommended First Phase 1 Coding Task

Implement a typed, side-effect-free settings boundary while preserving current
runtime behavior and schema.

The task should:

1. introduce one cached `Settings` object with typed values for database URL,
   internal scheduler enablement, Brave credential, environment name, and
   operational command overrides;
2. preserve SQLite as the explicit local/test default but reject a missing
   database URL when the environment is production;
3. centralize PostgreSQL URL normalization;
4. ensure secret values are excluded/redacted from representations;
5. make tests construct/reset settings deterministically;
6. wire only `app.database` and `app.main` initially;
7. add characterization tests proving imports still use in-memory SQLite in
   tests and the scheduler remains disabled;
8. perform no schema change, migration, scheduler change, or discovery
   refactor.

This is the smallest useful foundation because Alembic, app factories,
workers, and tenant-aware repositories all need one authoritative
configuration source. It is independently reversible and can be tested
without accessing a database or network beyond the existing isolated startup
suite.

## 16. Files Likely to Change in the First Task

Expected:

- `requirements.txt` — add only the justified typed-settings dependency if
  the installed Pydantic version does not already provide the chosen API.
- `app/core/__init__.py` — package marker.
- `app/core/config.py` — typed settings, validation, URL normalization, cache
  reset hook for tests.
- `app/database.py` — consume typed database settings while preserving engine
  behavior.
- `app/main.py` — consume the typed scheduler flag.
- `.env.example` — document non-secret settings and safe defaults.
- `tests/unit/test_settings.py` — precedence, validation, URL normalization,
  boolean parsing, and redaction.
- `tests/conftest.py` — only the minimum cache-reset/test-environment wiring
  required before application imports.
- `tests/routes/test_startup.py` — preserve isolated startup assertions.

Explicitly out of scope for that first task:

- Alembic files or schema changes;
- organization/user models;
- route restructuring;
- scanner/discovery changes;
- scheduler or workflow changes;
- production secrets or Render settings.
