# Phase 1 Schema Lifecycle Cutover Plan

## 1. Executive Summary

Opportunity Radar currently has two schema lifecycle systems operating at the
same time. Alembic revision `20260731_01` can create the four declared ORM
tables (`opportunities`, `sources`, `leads`, and `proposals`) on a fresh
database, but application startup, scanner and discovery commands, seed and
historical migration utilities, and the discovery scheduler can still create
or alter schema at runtime. Tests also create schemas directly from ORM
metadata.

Immediate removal of `Base.metadata.create_all()` would be unsafe. Existing
local SQLite databases may rely on implicit table creation and eleven
scanner-owned `sources` column repairs. The deployed PostgreSQL shape has not
been compared with the ORM or Alembic baseline. The `automation_job_state`
table is active but absent from ORM metadata and Alembic. Runtime metadata is
also import-dependent: normal web/scanner imports register `Opportunity` and
`Source`, whereas Alembic explicitly imports the lead and proposal modules to
register all four tables.

No existing database should be stamped or migrated on the strength of the
fresh-database baseline alone. Production cutover requires an approved,
schema-only PostgreSQL snapshot, an object-by-object comparison, a tested
backup/restore procedure, evidence about legacy SQLite shapes, and a decision
for scheduler state. The safe transition is:

1. add read-only schema status and comparison tooling;
2. inventory and approve deployed and legacy schema shapes;
3. reconcile Alembic head with those shapes using additive revisions;
4. move application integration tests to migration-built databases;
5. deploy an explicit, single-runner migration step and fail-closed startup
   preflight;
6. remove runtime `create_all()` and DDL one path at a time;
7. retire manual migration scripts only after operational-use confirmation;
8. remove DDL privileges from runtime database roles.

Alembic migrations must never run automatically in a web process. Application
startup should perform a read-only compatibility check and fail closed with a
sanitized, actionable error when schema state is incompatible.

## 2. Current Boundaries and Data Classes

| Boundary | Current implementation | Cutover target |
|---|---|---|
| Fresh database initialization | Alembic baseline exists, while several commands also call `create_all()` | An explicit operator/deployment `alembic upgrade head`; application startup never creates schema |
| Existing local SQLite | Runtime `create_all()` plus `ensure_sqlite_schema()` may repair old shapes | Back up, inspect, classify, and run a tested legacy reconciliation path; never guess or stamp on table names alone |
| Existing deployed PostgreSQL | Shape is unknown; runtime `create_all()` may create absent tables but not reconcile columns | Approved schema-only snapshot, diff, backup/restore rehearsal, then reviewed stamp or additive reconciliation migration |
| Test databases | Session-scoped in-memory URL plus fixture-level `create_all()`; dedicated Alembic lifecycle tests use disposable files | Keep narrowly scoped metadata tests; migration-build route/persistence integration databases |
| Standalone scripts | Scanner, discovery, seed, and four migration-like scripts own DDL | Commands assume a compatible schema and use a shared read-only preflight; historical scripts retired |
| Scheduler state | `scheduled_discovery` creates raw `automation_job_state` | ORM-described, Alembic-managed scheduler/job state, later evolved into durable jobs/runs/leases |
| ORM-managed schema | Four model tables, but registration depends on imports | One explicit metadata registration module used by Alembic, test infrastructure, and schema tooling |
| Non-ORM schema | `automation_job_state`; local JSON/lock/log files are state but not database schema | Database scheduler state becomes migration-owned; file state is separately retired with scheduler consolidation |

## 3. Complete Schema-Management Inventory

Line numbers reflect the repository at commit `74ccbe9`.

| Location | Symbol / execution trigger | Dialect assumptions and objects | ORM-managed / active / production relevance | Disposition |
|---|---|---|---|---|
| `app/core/config.py:10-63` | `load_settings()` defaults to `sqlite:///./opportunities.db` | URL normalization supports SQLite and PostgreSQL | Configuration boundary; active; all environments | **Keep**, then add an explicit safe operational mode so migration/status commands cannot silently use the default file |
| `app/database.py:11-31` | Import constructs global engine and `SessionLocal` | SQLite gets `check_same_thread=False`; otherwise generic SQLAlchemy | Infrastructure, active, production-relevant | **Keep temporarily**; later expose factories so inspection and migration tools need not import a process-global engine |
| `app/main.py:62` | Module import calls `Base.metadata.create_all(bind=engine)` | SQLAlchemy dialect-neutral creation; metadata normally has web-imported models | ORM-managed; active on every web import; production-relevant | **Replace with Alembic**, only after preflight, deployment migration, and compatibility evidence exist |
| `app/main.py:36-51` | FastAPI lifespan starts/stops internal scheduler when enabled | No direct DDL, but scheduled scanner can run DDL | Active locally by setting; disabled in Render | **Isolate** scheduler from web; test that lifespan never mutates schema |
| `app/scanner.py:52-92` | `ensure_sqlite_schema()` at start of every `run_scanner()` (`:358-359`) | Calls `create_all()` on every dialect; SQLite-only inspection and `ALTER TABLE` | Mixed ORM/runtime repair; active in daily workflow and manual scan; production call reaches PostgreSQL `create_all()` | **Keep temporarily**, then split into read-only preflight and Alembic/legacy conversion |
| `app/scanner.py:97-132` | `approve_existing_sources()` during every scan | Data repair, not DDL; fills source defaults and approval flags | Active and production-relevant hidden data migration | **Isolate** as an explicit, idempotent data migration/admin command before removing schema repair |
| `app/source_discovery.py:192-205` | `RwandaSourceDiscovery.run()` calls `create_all()` | ORM table creation; import set normally registers opportunity/source tables | Active through website workflow; production-relevant | **Replace with Alembic** after workflow preflight is available |
| `app/seed_sources.py:92-96` | `seed_sources()` calls `create_all()` | ORM creation, then Rwanda-specific source upserts | Callable utility; operational use unproven | **Isolate**, then require migrated schema; later replace seed with versioned country-pack import |
| `app/migrate_phase2.py:8-155` | Direct execution of `migrate()` | Inspector plus SQLite-shaped `ALTER TABLE`; data backfills | Historical handwritten migration; production use unknown | **Retire** after legacy shapes and operational references are inventoried |
| `app/migrate_opportunity_leads.py:6-69` | Direct execution of `migrate()` | Inspector and `ALTER TABLE opportunities ADD COLUMN` | Historical handwritten migration; production use unknown | **Retire** after compatibility fixture and reconciliation migration exist |
| `app/migrate_leads.py:8-48` | Direct execution of `migrate()` | Inspector, row count, possible `DROP TABLE proposals`, targeted `create_all()` | Historical, potentially destructive; operational use unknown | **Quarantine immediately** in documentation; later archive/delete after deployed-state review |
| `app/migrate_proposals.py:8-34` | Direct execution of `migrate()` | Inspector and targeted `create_all()` | Historical utility; operational use unknown | **Retire** after schema reconciliation |
| `app/scheduled_discovery.py:18-30` | `_ensure_state_table()` on every workflow invocation | Raw `CREATE TABLE IF NOT EXISTS`; PostgreSQL-oriented timestamp | Non-ORM; active production workflow; production-relevant | **Replace with ORM plus Alembic migration**; leave temporarily until existing table shape is reconciled |
| `app/scheduled_discovery.py:32-69` | Reads/upserts scheduler success state | `ON CONFLICT`; assumes one `job_name` primary key | Active; production-relevant; not a lease | **Replace** with modeled durable state and eventually runs/leases |
| `app/internal_scheduler.py:45-228` | Web-owned thread and file state | No database DDL directly; invokes scanner path with DDL | Active where enabled, Render-disabled; local schema relevance | **Retire from web** after one scheduler/worker path exists |
| `app/jobs.py:15-51` | Internal scheduler job wrappers | Tender wrapper reaches scanner DDL; discovery subprocess target is missing | Callable only through internal scheduler; broken discovery path | **Replace** with canonical worker commands; do not activate |
| `app/automation.py:89-124` | Standalone file-backed scheduler | Invokes scanner/discovery subprocesses; scanner/discovery then run DDL | No tracked production trigger; operational use unknown | **Investigate**, then retire after operator confirmation |
| `tests/conftest.py:98-129` | `isolated_session_factory` creates in-memory SQLite tables | `StaticPool`; metadata contents depend on modules imported during collection | ORM-managed test schema; active | **Keep narrowly**, but remove import-order dependence and migrate integration fixtures to Alembic |
| `tests/migrations/test_migrations.py:223-270` | Alembic command API on unique disposable SQLite files | Explicit SQLite URL; upgrades/downgrades and introspects | Migration-managed; active test-only | **Keep and extend**; add legacy-shape and PostgreSQL suites later |
| `migrations/env.py:8-30` | Loads typed settings and explicitly registers all model modules | Offline/online, SQLite batch mode, future PostgreSQL | Canonical Alembic metadata; active tooling | **Keep**; later consume a dedicated metadata registration boundary |
| `migrations/versions/20260731_01_baseline_current_schema.py` | Revision `20260731_01` | Creates/drops four ORM tables and indexes | Alembic-managed; fresh databases only | **Keep as fresh baseline** unless deployed comparison proves a reviewed correction is required; never edit after shared adoption |
| `.github/workflows/daily-tender-scan.yml:34` | Scheduled `python -m app.scanner` | Production URL secret; scanner calls runtime DDL | Active by repository configuration | **Keep temporarily**; require schema preflight after cutover |
| `.github/workflows/website-discovery.yml:38` | Scheduled `python -m app.scheduled_discovery` | Production URL secret; creates scheduler table | Active by repository configuration | **Keep temporarily**; replace runtime DDL before removing its fallback |
| `render.yaml:12-24` | Build installs packages; web start imports `app.main` | Render PostgreSQL, scheduler disabled | Active deployment; startup currently invokes `create_all()` | **Update in a later phase** with one controlled migration step and schema preflight |
| `current_backend_files.txt:1-1923` | Concatenated historical source snapshot | Contains an old scanner `create_all()` occurrence | Not executable; inactive | **Document as historical artifact**, not a schema path; investigate retention separately |

No `PRAGMA` statements, application `DROP TABLE` paths other than
`app/migrate_leads.py`, Docker/Compose files, Render migration command, or
other Alembic command integrations were found. Alembic `op.drop_table()`
exists only in the baseline downgrade. `DEPLOYMENT.md` describes Render cron
jobs that are absent from `render.yaml` and is operationally stale.

## 4. `create_all()` Call Matrix

| Call | Purpose / trigger | Risk if removed now | Risk if retained | Removal prerequisites | Replacement and tests |
|---|---|---|---|---|---|
| `app/main.py:62` | Make web schema exist at module import | Fresh/manual installs and unmigrated deployments may fail; local default file may no longer self-initialize | Import-time DDL, drift, incomplete metadata, multi-instance race, excessive runtime privilege | All supported DBs reconciled and versioned; release migration step; startup status check; documented local initialization | Explicit `alembic upgrade head`; startup tests for empty/behind/ahead/current schemas and no DDL |
| `app/scanner.py:59` | Ensure scanner tables before repair and scan | Daily/manual scanner fails on legacy/uninitialized databases | Every scan can create schema; PostgreSQL runtime role retains DDL; hides deployment defects | Scanner preflight; legacy SQLite conversion; production at head; remove data repair coupling | Read-only compatibility check; tests that scanner rejects incompatible schema without mutation |
| `app/source_discovery.py:203` | Ensure source table before discovery | Standalone workflow may fail if deployment was never initialized | Workflow creates partial/import-dependent schema and masks missing migration | Workflow migration dependency and preflight; all environments at head | Explicit release migration; discovery command compatibility tests with no DDL |
| `app/seed_sources.py:93` | Let seed utility bootstrap schema | Operators may rely on one-command seed | Seed becomes an implicit schema manager and embeds policy with initialization | Inventory operator use; document initialization; migrated test DB | Administrative import command that refuses non-head schema; idempotency tests |
| `app/migrate_leads.py:33` | Create only `leads` after optionally dropping empty proposals | Script ceases to work on old databases | Destructive shadow migration remains callable; schema graph can diverge | Legacy/deployed shape inventory; replacement reconciliation revision; operational retirement approval | Archive/delete script; migration tests from each recorded legacy fixture |
| `app/migrate_proposals.py:18` | Create only `proposals` | Script ceases to bootstrap dormant table | Bypasses revision history and can create a partial schema | Same as above | Archive/delete; migration compatibility tests |
| `tests/conftest.py:116` | Fast, isolated ORM persistence/route fixtures | Tests would need migrated fixtures and may become slower | Integration tests can pass on metadata while migrations are broken; metadata depends on collection imports | Reusable migrated fixture and clear unit/integration split | Keep for isolated model tests; use Alembic-to-head for route/persistence integration tests |

The call in `app/source_discovery.py` and those in ordinary web/scanner paths
do not explicitly import `app.lead_models` and `app.proposal_models`.
Therefore, their metadata may contain only `opportunities` and `sources` in a
fresh process. Alembic deliberately imports all three model modules and
creates four tables. Cutover tests must make registration explicit and must
not depend on pytest collection order.

## 5. Runtime Schema-Repair Analysis

### 5.1 `app.scanner.ensure_sqlite_schema()`

`run_scanner()` invokes this function before any source approval, expiry, or
network work. It first runs `create_all()` for every dialect. Only when the
engine dialect is SQLite does it inspect `sources` and add missing columns.
It is additive at the column-existence level, but it does not validate column
type, nullability, default, constraints, indexes, or a partially failed prior
repair. Dynamic SQL uses constant repository-owned names, not user input.

| Repaired `sources` column | Legacy SQLite DDL | In baseline `20260731_01` | Future owner / disposition |
|---|---|---|---|
| `last_discovered_at` | `DATETIME` | Yes | Alembic; retain repair only for classified legacy SQLite shapes |
| `last_scan_started_at` | `DATETIME` | Yes | Alembic |
| `last_opportunity_found_at` | `DATETIME` | Yes | Alembic |
| `last_scan_duration_seconds` | `FLOAT` | Yes | Alembic |
| `last_http_status` | `INTEGER` | Yes | Alembic |
| `total_opportunities_found` | `INTEGER NOT NULL DEFAULT 0` | Yes | Alembic |
| `scan_interval_hours` | `INTEGER NOT NULL DEFAULT 12` | Yes | Alembic |
| `priority_score` | `FLOAT NOT NULL DEFAULT 50` | Yes | Alembic |
| `url_relevance_score` | `FLOAT NOT NULL DEFAULT 0` | Yes | Alembic |
| `is_auto_disabled` | `BOOLEAN NOT NULL DEFAULT 0` | Yes | Alembic |
| `disabled_reason` | `VARCHAR(500)` | Yes | Alembic |

The function is not required as a PostgreSQL column repair: its ALTER branch
never runs there. Its preceding `create_all()` is still production-relevant
and unsafe because it can create missing tables without establishing an
Alembic revision. Removing the SQLite branch can break old files that contain
an early `sources` table. Before removal, construct sanitized fixtures for
each observed legacy shape, back them up, run a reviewed reconciliation path,
and prove row counts and values are preserved.

### 5.2 Other hidden repair behavior

- `approve_existing_sources()` is a data migration concealed inside every
  scan. It automatically approves active sources and fills five defaults.
  Schema cutover must not accidentally change this behavior; extract it into
  an explicit data operation before scanner DDL removal.
- `migrate_phase2.py` backfills dates, status, and expiry after adding six
  columns. The baseline contains those columns but cannot establish whether
  an existing row was backfilled correctly.
- `migrate_opportunity_leads.py` adds eight flattened pipeline columns. All
  are in the baseline, while separate `leads` rows may also exist. Schema
  compatibility and data consistency are different checks.
- `Base.metadata.create_all()` never changes an existing column, constraint,
  or index. Its success is not evidence that a database matches Alembic head.
- Runtime engine/session construction is not DDL by itself, but importing
  `app.main` immediately follows it with schema creation.

### 5.3 Scheduler-state table

`app.scheduled_discovery._ensure_state_table()` creates:

```sql
automation_job_state(
    job_name VARCHAR(100) PRIMARY KEY,
    last_success_at TIMESTAMP WITH TIME ZONE
)
```

The table is absent from `Base.metadata` and revision `20260731_01`. It is
therefore neither checked by metadata parity tests nor removed by baseline
downgrade. Its `ON CONFLICT` update records only successful completion; there
is no running lease, attempt history, owner, heartbeat, or stale-run recovery.

**Preferred decision:** represent scheduler state as an ORM model and manage
it with a new additive Alembic migration, then evolve it toward durable job
runs and leases. The immediate model should preserve the existing table name
and two-column contract unless the deployed snapshot requires reconciliation.

Advantages:

- complete metadata and schema comparisons include the active table;
- SQLite and PostgreSQL behavior can be tested through SQLAlchemy;
- runtime DDL can be removed without changing discovery cadence semantics;
- a later expand/migrate/contract change can add attempts and leases.

Risks and implications:

- the deployed table may have a different type/default/constraint shape;
- simply creating a migration will fail where the table already exists;
- a reconciliation migration must distinguish absent, exact-match, and drifted
  tables and must never discard the last-success value;
- the current table is a gate, not concurrency control, so modeling it does
  not solve duplicate long-running executions.

Tests must cover absent and existing compatible tables, timestamp round-trip
on SQLite/PostgreSQL, preservation of `website_discovery`, idempotent success
updates, schema drift rejection, and later lease concurrency. Until those
tests and a deployed snapshot exist, leave scheduler ownership in place and
do not remove `_ensure_state_table()`.

## 6. Manual Migration-Script Assessment

| Script | Historical purpose and affected data | Idempotency / method | Baseline overlap | Operational status and retirement |
|---|---|---|---|---|
| `app/migrate_phase2.py` | Adds `status`, `is_expired`, `user_notes`, `first_discovered_at`, `last_seen_at`, `updated_at` to `opportunities`; backfills values | Column-presence checks make additions rerunnable; data updates rerun; direct SQL; assumes `created_at` exists and SQLite-like DDL | Complete column overlap | Use unknown. Freeze as reference. Retire after every supported legacy shape has a tested Alembic reconciliation and operators confirm it is unused |
| `app/migrate_opportunity_leads.py` | Adds eight flattened lead/pipeline columns to `opportunities` | Column-presence checks and final verification; direct SQL; additive but no transactional cross-dialect guarantee | Complete column overlap | Freeze; preserve a legacy fixture; archive/delete only after reconciliation and pipeline data audit |
| `app/migrate_leads.py` | Creates normalized `leads`; first drops `proposals` if present and empty | Returns if `leads` exists; may destructively drop an empty table; targeted `create_all()` | `leads` and `proposals` are both in baseline | Treat as unsafe for further use. Confirm no runbook/workflow invokes it, then remove after deployed snapshot and compatibility migration approval |
| `app/migrate_proposals.py` | Creates normalized `proposals` | Returns when table exists; targeted `create_all()` | Complete table overlap | Freeze and retire with the same evidence |

None participates in the Alembic revision graph. None records execution. The
scripts should not be moved into an `archive/` package that remains easily
executable. Preserve history in Git and the cutover documentation; delete
them in separate reviewable commits once usage and compatibility acceptance
criteria are satisfied.

## 7. Existing-Database Compatibility Strategy

### A. Fresh database

1. Require an explicit, non-default database URL and verified environment.
2. Run `alembic upgrade head` from one controlled release/migration process.
3. Verify the revision equals the application-supported head and all required
   tables are present.
4. Start the application only after the migration succeeds.
5. Application startup performs read-only status validation; an empty
   database fails closed with instructions and never self-creates tables.

The fresh path is already smoke-tested on disposable SQLite, but must also be
tested on disposable PostgreSQL before production cutover.

### B. Existing local SQLite database

1. Stop all application, scanner, discovery, and scheduler processes.
2. Copy the database and sidecar files using an SQLite-safe backup procedure;
   retain hash, size, timestamp, and source path outside the repository.
3. Inspect the copy, never the original: SQLite version, integrity check,
   tables, columns, declared types, defaults, nullability, PK/FK/unique/index
   objects, row counts, and current scheduler state if present.
4. Compare it with known legacy shapes, current ORM metadata, and Alembic
   head. Extra columns/tables must be classified, not deleted automatically.
5. If it exactly matches a reviewed target schema except for
   `alembic_version`, a stamp may be proposed, but only after an automated
   fingerprint match and human approval. Table-name equality is insufficient.
6. If columns or constraints are missing, run a reviewed additive
   reconciliation migration on another copy. SQLite batch operations require
   explicit data-copy, FK, index, and row-count validation.
7. Rehearse restore, then repeat on the real local file only with explicit
   user approval and a retained backup.

Never stamp a partially matching SQLite database. Never use the repository's
real `opportunities.db` as a fixture.

### C. Existing deployed PostgreSQL database

1. Obtain an approved schema-only snapshot using the procedure below.
2. Verify database identity and environment with a read-only account before
   capture; record server/database identity without credentials.
3. Compare tables, sequences, types, defaults, constraints, indexes, and the
   presence/contents shape of `alembic_version` and `automation_job_state`.
4. Restore the schema-only snapshot into an isolated review database where
   possible, then run read-only tooling and proposed migrations there.
5. If the schema is an exact approved match for the baseline, propose a
   schema-neutral stamp in a separate production change with backup and
   rollback approval.
6. If it differs, create additive reconciliation revisions. Do not edit the
   meaning of an adopted revision or pretend drift is baseline-compatible.
7. Validate data preservation on a sanitized staging copy, including row
   counts, null populations, duplicate candidates, FK orphans, unique-index
   conflicts, and application smoke tests.

Whether to stamp or reconcile is an evidence-based decision. Until the
snapshot review is complete, neither is approved.

### D. Test databases

- Keep `Base.metadata.create_all()` only for pure model/unit tests whose
  purpose is ORM behavior independent of migration history.
- Make metadata registration explicit in those fixtures; do not rely on
  collection order to import `Lead` and `Proposal`.
- Build route, repository, scanner-persistence, and other application
  integration databases by upgrading a disposable database to Alembic head.
- Retain dedicated migration lifecycle tests for upgrade, downgrade, and
  schema parity.
- Add PostgreSQL migration-backed integration tests before relying on server
  defaults, timezone types, concurrency, locking, or future RLS.
- Never let test configuration fall back to `opportunities.db` or a
  PostgreSQL URL not created by the test job.

## 8. Production Schema-Only Snapshot Procedure

This is a future operator checklist; no command in this section was executed
during this planning task.

1. Obtain a change ticket and approvals from the database owner and security
   owner. Define the expected environment, database name, maintenance impact,
   snapshot custodian, retention, and deletion date.
2. Use a dedicated least-privilege inspection account. Supply credentials via
   an approved secret manager or ephemeral environment injection; never put a
   URL/password in shell history, command transcripts, documents, or Git.
3. From a secured workstation or controlled job, verify identity before dump:

   ```powershell
   psql "$env:APPROVED_SCHEMA_DATABASE_URL" -X -v ON_ERROR_STOP=1 -c "SELECT current_database(), current_user, inet_server_addr(), inet_server_port(), version();"
   ```

   A second person must compare the result with the approved ticket. Stop on
   any mismatch.
4. Capture schema only, excluding ownership and privilege statements:

   ```powershell
   pg_dump --schema-only --no-owner --no-privileges --format=plain --file "<secure-untracked-path>/opportunity-radar-<environment>-<UTC-timestamp>-schema.sql" "$env:APPROVED_SCHEMA_DATABASE_URL"
   ```

   If extensions or provider-managed schemas must be excluded, agree exact
   `--schema`/`--exclude-schema` arguments before capture and record them.
5. Confirm the artifact contains no table data and no credential. Treat names,
   comments, function bodies, and topology as sensitive even though data rows
   are excluded.
6. Record PostgreSQL and `pg_dump` versions, UTC capture time, environment,
   database identity, command options, SHA-256, encrypted storage location,
   reviewer, and expiry date in the ticket—not in the repository.
7. Store the dump encrypted with access logging. Do not email it, put it in a
   general chat channel, or commit it.
8. Have database and application reviewers classify every difference against
   ORM metadata and Alembic head. Require explicit approval of any stamp or
   reconciliation plan.
9. Delete the snapshot according to the approved retention policy after the
   review and retain only a non-sensitive comparison report where allowed.

## 9. Schema Comparison Procedure

Produce three normalized manifests using read-only tooling:

1. **Deployed PostgreSQL manifest:** derived from the approved schema-only
   dump restored to isolation or from approved catalog inspection.
2. **Alembic-head manifest:** create a disposable empty database, upgrade to
   head, and inspect it.
3. **ORM manifest:** explicitly register all model modules and serialize
   `Base.metadata` without importing `app.main`.

For each supported legacy SQLite shape, produce a fourth manifest from a
sanitized copy. Compare:

- schemas, tables, and sequences;
- columns, ordinal position, stable and dialect-native types;
- server and client defaults separately;
- nullability and generated/identity behavior;
- primary keys, foreign keys, targets, update/delete actions;
- unique constraints and unique indexes;
- non-unique indexes, column order, expressions, predicates, and methods;
- checks, exclusions, views, triggers, functions, and extensions when present;
- Alembic revision rows and unmodeled objects such as scheduler state.

Normalize only documented dialect differences. For example, SQLite boolean
storage and PostgreSQL `BOOLEAN`, or SQLite datetime affinity and PostgreSQL
`TIMESTAMP WITH TIME ZONE`, may be expected only when semantics and tests
agree. Do not normalize away nullability, uniqueness, cascades, defaults, or
index coverage.

Classify each difference:

| Class | Meaning | Required action |
|---|---|---|
| Expected dialect difference | Different representation with proven equivalent behavior | Document normalization and retain cross-dialect tests |
| Missing migration | ORM/intended schema is absent from Alembic history | Add reviewed migration and fresh/upgrade tests |
| Legacy artifact | Known historical object no longer intended | Preserve until data/usage proof supports an explicit retirement migration |
| Production drift | Deployed object differs from approved history | Root-cause, assess data impact, reconcile additively where possible |
| Destructive conflict | Reconciliation would drop, narrow, rewrite, or reject data | Stop; require backup, data remediation, rehearsal, and separate approval |
| Unknown requiring review | Purpose or ownership is unclear | Stop; identify owner and evidence before any stamp or migration |

A schema fingerprint is a decision aid, not authorization. Human review is
required for production stamping and destructive differences.

## 10. Ordered Cutover Phases

### Phase A — Read-only schema inspection and status boundary

- **Scope:** Add a side-effect-free schema manifest/status library accepting
  an explicit connection; no global engine, default URL, or application
  startup integration.
- **Likely files:** new `app/schema_status.py`, new focused tests under
  `tests/migrations/`; possibly a non-production CLI in a later task.
- **Acceptance:** empty/current/missing-version/drifted disposable SQLite
  shapes are classified deterministically; no DDL or network.
- **Rollback:** delete the isolated module/tests.
- **Production risk:** Low; not wired to runtime.
- **Commit:** Separate commit required.

### Phase B — Obtain and approve deployed and legacy schema evidence

- **Scope:** Approved PostgreSQL schema-only snapshot and sanitized legacy
  SQLite manifests; no migration execution on source databases.
- **Likely files:** secure external evidence; only a sanitized, non-sensitive
  comparison report if approval permits.
- **Acceptance:** every object is classified; database identity, snapshot
  hash, reviewers, and restore rehearsal are recorded.
- **Rollback:** delete secured working copies under retention policy; no DB
  change.
- **Production risk:** Low read load and sensitive metadata handling.
- **Commit:** Documentation/evidence commit only if sanitized and approved.

### Phase C — Reconcile Alembic head

- **Scope:** Additive revisions for proven drift and modeled
  `automation_job_state`; explicit compatibility decision for existing exact
  schemas.
- **Likely files:** ORM model/registration, `migrations/versions/`, migration
  tests, migration README.
- **Acceptance:** fresh and every supported legacy/deployed-copy path reach
  identical intended head with row counts and scheduler state preserved.
- **Rollback:** Restore disposable copy during testing; production rollback is
  a separately rehearsed forward fix or restore, never an assumed downgrade.
- **Production risk:** High until staging proof.
- **Commit:** One reviewable commit per logical migration.

### Phase D — Move integration tests to migration-backed databases

- **Scope:** Route and persistence fixtures upgrade disposable databases to
  head; keep direct metadata creation only for explicit ORM unit tests.
- **Likely files:** `tests/conftest.py`, route/integration fixtures, migration
  helpers, CI workflow later.
- **Acceptance:** no collection-order metadata dependency; full suite passes
  on SQLite; PostgreSQL migration suite passes in isolated CI.
- **Rollback:** Revert fixture commit to prior direct metadata setup.
- **Production risk:** None; test-only.
- **Commit:** Separate commit required.

### Phase E — Add deployment migration and remove web-startup `create_all()`

- **Scope:** One controlled pre-deploy/release migration runner, read-only web
  startup preflight, removal of `app/main.py:62` only.
- **Likely files:** `app/main.py`, schema-status boundary, `render.yaml` or an
  approved release workflow, `DEPLOYMENT.md`, startup tests.
- **Acceptance:** current head starts; empty/behind/ahead/unknown fails closed;
  multiple web instances never run migrations; staging rollout and rollback
  rehearsal pass.
- **Rollback:** Roll application back only to a version compatible with the
  forward schema; restore prior deployment command if no schema change ran.
- **Production risk:** High; local implicit initialization changes.
- **Commit:** Separate commit required.

### Phase F — Retire scanner/discovery/seed runtime DDL

- **Scope:** Replace `ensure_sqlite_schema()` with preflight, remove
  `create_all()` from scanner/discovery/seed, extract source data repair.
- **Likely files:** `app/scanner.py`, `app/source_discovery.py`,
  `app/seed_sources.py`, schema-status service, tests and docs.
- **Acceptance:** commands never execute DDL; compatible DB works; legacy and
  incompatible DBs fail safely before network or mutation.
- **Rollback:** Restore one compatibility release only while migrated schema
  remains backward-compatible.
- **Production risk:** Medium-high for old local files and scheduled jobs.
- **Commit:** Separate small commits per entry point.

### Phase G — Retire manual migration scripts

- **Scope:** Confirm no workflow/runbook/operator uses four scripts, preserve
  legacy fixtures and Git history, then delete scripts.
- **Likely files:** four `app/migrate_*.py` files and documentation.
- **Acceptance:** repository/workflow search clean; each historical shape has
  an Alembic-supported path; operator sign-off recorded.
- **Rollback:** Restore script source from Git for analysis only; do not run it
  on a database.
- **Production risk:** Low after evidence; high if usage is unknown.
- **Commit:** Separate commit required.

### Phase H — Harden operations and runtime privileges

- **Scope:** Update deployment/runbooks, grant DDL only to migration role,
  require schema status observability, and consolidate schedulers/workers.
- **Likely files:** `render.yaml`, workflows, `DEPLOYMENT.md`, scheduler/job
  entry points, environment documentation.
- **Acceptance:** one migration runner; app/scanner roles cannot create/alter/
  drop; dashboards/alerts show revision mismatch; rollback drill passes.
- **Rollback:** Restore privileges only through incident-approved procedure;
  never re-enable automatic web migrations.
- **Production risk:** Medium; incorrect grants can cause outage.
- **Commit:** Separate commits for deployment, privileges/runbook, and worker
  consolidation.

## 11. Failure and Rollback Design

| Observed state | Required behavior |
|---|---|
| Empty database | Application and workers fail closed with “initialize using approved migration command”; only explicit migration runner may create schema |
| Behind Alembic head | Fail closed before serving traffic or running jobs; report current/required revisions without URL or credentials |
| Ahead of application migrations | Fail closed; deploy compatible/newer application or investigate unknown revision; never downgrade automatically |
| `alembic_version` absent and no managed tables | Classify as empty; do not stamp from application startup |
| `alembic_version` absent but managed tables exist | Classify as unversioned existing schema; require fingerprint comparison and approval |
| Schema exists but version claims head and objects differ | Classify as drift; fail closed and investigate |
| Multiple/unknown version rows or partial migration | Fail closed; preserve evidence, inspect migration logs/transaction state, restore or apply reviewed forward repair |
| Migration fails | Migration runner exits nonzero; deployment does not start/advance; capture sanitized diagnostics; assess transactional rollback, restore, or forward repair |
| Application starts against incompatible schema | Read-only preflight prevents readiness; process exits or remains unready; no DDL or automatic recovery |

**Recommendation:** never automatically migrate from web startup, scanner
startup, scheduler startup, or ordinary application commands. Run migrations
once in a controlled release/pre-deploy job with a dedicated role. Startup
checks status only and fails closed. Automatic migration in multiple web
instances creates races, ambiguous ownership, uncontrolled lock duration, and
an unsafe coupling between availability and schema mutation.

Downgrade scripts are development validation, not a production rollback
promise. Prefer backward-compatible expand/migrate/contract releases so the
previous application can run against the forward schema. For destructive or
data-transforming changes, rollback requires a rehearsed forward repair or
database restore with explicit recovery-point objectives.

## 12. Deployment Integration

Current deployment behavior:

- `render.yaml` installs dependencies during build and starts
  `uvicorn app.main:app`; it has no migration, release, pre-deploy, worker, or
  cron service declaration.
- Importing `app.main` runs `create_all()` today.
- Render disables the internal scheduler.
- GitHub Actions directly run the scanner daily and scheduled discovery every
  five calendar-day cron dates with production database secrets.
- `DEPLOYMENT.md` incorrectly says Render creates two cron jobs and describes
  an obsolete daily discovery trigger.

Target deployment behavior:

1. Build an immutable artifact without contacting a database or running
   migrations.
2. Run migrations once in a dedicated release/pre-deploy job using a
   migration-only credential and explicit environment identity safeguards.
   Confirm the hosting platform's supported pre-deploy mechanism before
   changing `render.yaml`; otherwise use a protected deployment workflow.
3. Acquire a PostgreSQL advisory lock or equivalent single-runner control in
   addition to deployment concurrency. Set statement/lock timeouts and emit a
   migration correlation ID.
4. Start web instances only after migration success. Web credentials should
   lack DDL rights and readiness should validate schema status.
5. Start workers/schedules after the same gate. They never migrate.

Migrations do not belong in dependency build steps because builds should be
repeatable and environment-independent. They do not belong in every web
startup because multiple instances may race and restart. A release/pre-deploy
step is preferred.

Required environment separation:

- explicit application environment and database identity;
- separate migration and runtime database roles;
- explicit `DATABASE_URL` with no local fallback outside local/test;
- scheduler disabled in web processes;
- secrets injected without logging;
- revision compatibility range for rolling deployments.

Rollback planning must account for a new application failing after a
successful migration. The previous artifact must remain compatible with the
expanded schema. Contracting/dropping objects occurs only in a later release
after old instances and rollback windows are gone.

## 13. Test Transition Plan

Add tests incrementally for:

1. fresh Alembic upgrade to head on disposable SQLite and PostgreSQL;
2. repeat upgrade, downgrade/re-upgrade for development correctness;
3. exact current revision and complete schema manifest;
4. each sanitized compatible legacy SQLite shape reaching head with data
   preserved;
5. existing exact schema without `alembic_version` being classified as
   “review required,” never automatically stamped;
6. missing, empty, multiple, ahead, unknown, and behind revision states;
7. claimed-head schema drift in a column, constraint, index, or table;
8. application startup succeeds at supported head without DDL;
9. application startup fails before routes/jobs on outdated or drifted schema;
10. scanner, discovery, and seed commands execute no DDL;
11. scheduler state creation through migration, existing-state preservation,
    timestamp behavior, and later lease concurrency;
12. direct model tests explicitly registering required models;
13. integration fixtures using Alembic rather than `create_all()`;
14. configuration rejecting default local SQLite in migration/production
    modes and rejecting non-test PostgreSQL in tests;
15. test network, subprocess, scheduler, and real-local-file guards remaining
    effective.

PostgreSQL tests must use a database created for the test job with no route or
credential to production. SQLite remains useful for fast compatibility tests
but cannot prove PostgreSQL locks, sequences, timezone semantics, transaction
behavior, index methods, or future RLS.

## 14. Risk Register

| Risk | Likelihood | Impact | Mitigation | Evidence required |
|---|---|---|---|---|
| Stamp incompatible production schema | Medium | Critical data loss/outage | Exact normalized fingerprint, two-person review, backup/restore rehearsal, separate approval | Approved snapshot and zero unclassified differences |
| Hidden schema drift | High | Critical | Compare deployed, Alembic, ORM, and legacy manifests | Object-level diff including defaults/indexes/sequences |
| Lost indexes or constraints | Medium | High correctness/performance | Compare named and structural indexes/constraints; workload review | PostgreSQL catalog diff and query-plan checks |
| SQLite/PostgreSQL differences | High | High | Dual-dialect tests; narrow normalization | Disposable PostgreSQL suite evidence |
| Scheduler-state omission | High | High duplicate/early jobs | Model and migrate state; preserve last success; later leases | Existing table snapshot and scheduler tests |
| Concurrent migration execution | Medium | Critical | One release runner, advisory lock, dedicated role/timeouts | Concurrency rehearsal and deployment logs |
| Manual scripts still used | Medium | High drift/destruction | Search runbooks/workflows, operator sign-off, deprecation period | Usage inventory and replacement acceptance |
| Startup failure after removing `create_all()` | High for legacy installs | High | Preflight, local initialization docs, migrated fixtures, staged rollout | Empty/legacy/current startup matrix |
| Accidental default `opportunities.db` use | Medium | High local data modification | Explicit URL requirement for operational commands; deny known path in tests | Safety tests and command logs |
| Destructive downgrade assumption | Medium | Critical | Treat downgrade as test aid; forward-compatible releases and restore plan | Restore drill and compatibility matrix |
| Import-dependent metadata | High | High incomplete schema | One explicit model registry; tests in isolated process/order | Four-table manifest in every schema tool |
| Existing table without revision | High | Critical if auto-stamped | Classify as unversioned; no automatic stamp | Fingerprint plus human approval |
| Runtime role retains DDL | High today | High drift/security | Separate migration/runtime roles after cutover | Database grants audit |
| Partial SQLite table rebuild | Medium | High data/constraint loss | Copy rehearsal, FK/integrity checks, counts/hashes | Legacy fixture migration reports |
| Baseline edited after adoption | Low | Critical history divergence | Immutable adopted revisions; corrective revisions only | Revision checksum/review policy |
| Migration lock causes outage | Medium | High | Expand/contract, timeouts, table-size review, maintenance plan | Staging timing and lock observation |
| Sensitive schema dump leaked | Low-medium | High security | Encrypted approved storage, minimal access, no Git | Artifact access log and retention record |

## 15. Exact Next Coding Task

### Objective

Add a side-effect-free, read-only schema status boundary that classifies an
explicitly supplied disposable database as:

- empty;
- current at the expected Alembic head;
- behind;
- ahead/unknown revision;
- managed tables present without `alembic_version`;
- revision claims head but required schema objects drift.

It must not be wired into `app.main`, scanners, workflows, or deployment yet.

### Files to create or modify

- Create `app/schema_status.py` containing immutable result types and
  inspection functions that accept an SQLAlchemy `Connection` or `Engine` and
  an explicitly supplied expected revision/manifest.
- Create `tests/migrations/test_schema_status.py` using only in-memory or
  uniquely named disposable SQLite databases.
- If useful, minimally extend a test-only migration helper under
  `tests/migrations/`; do not change production configuration.

### Required tests

- empty database classification;
- fresh Alembic-head classification;
- schema tables without revision table;
- empty version table;
- behind and unknown revision values;
- head revision with a missing required table/column/index/constraint;
- inspection performs no DDL;
- no import of `app.main`, scanner, or scheduler;
- no network, PostgreSQL, default URL, or `opportunities.db` access.

### Forbidden changes

- no startup/runtime integration;
- no ORM or migration revision changes;
- no production URL or database access;
- no stamp, upgrade, or downgrade outside disposable test setup;
- no removal of `create_all()` or runtime repair;
- no deployment, scheduler, scanner, route, secret, or workflow changes.

### Validation commands

```powershell
.\venv\Scripts\python.exe -m pytest tests\migrations\test_schema_status.py -q
.\venv\Scripts\python.exe -m pytest tests\migrations -q
.\venv\Scripts\python.exe -m pytest --collect-only -q
git diff --check
git status --short --branch
```

### Acceptance criteria

1. All states above are deterministic and use structured, sanitized results.
2. Inspection accepts only an explicit engine/connection and never imports or
   constructs the global application engine.
3. The module performs only catalog/metadata reads.
4. Tests prove no application startup, scheduler, scanner, external network,
   PostgreSQL, default database, or real local file access.
5. No current runtime behavior changes.

This is the smallest low-risk step because it creates the evidence boundary
needed by legacy comparison, startup preflight, deployment gates, and eventual
runtime DDL removal without touching any real database or production entry
point.

## 16. Cutover Readiness Exit Criteria

Runtime schema lifecycle cutover is ready only when:

- the production snapshot and all supported legacy shapes have no
  unclassified differences;
- Alembic head represents every application-owned database object, including
  the approved scheduler-state design;
- fresh and legacy migration paths pass on SQLite and PostgreSQL;
- integration tests use migration-built databases;
- a single migration runner and read-only startup preflight are rehearsed;
- web, scanner, discovery, seed, and scheduler paths perform no DDL;
- runtime roles lack schema mutation privileges;
- manual scripts are no longer operational dependencies;
- backups, forward compatibility, restore, and failed-migration procedures
  have been exercised and approved.

Until every criterion is satisfied, retain the existing runtime protections
selectively, do not stamp production, and remove no schema path merely because
the fresh-database baseline passes.
