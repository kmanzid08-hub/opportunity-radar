# MVP release sprint validation — 30 October 2026

Work stayed on `global-saas-development`. The user explicitly authorized the existing
dirty tree. Existing modified, deleted and untracked work was preserved. No commit,
push, deployment, production connection, production configuration change, local data
upload, general live discovery, or real provider request was performed.

## Functional assessment

| Function | Before this sprint | Implemented result |
| --- | --- | --- |
| Authentication | Missing; private pages open | Invite activation, sign-in/out, expiring revocable sessions, password change and operator recovery |
| Workspace/company authorization | Blocking launch; queries unscoped | Server-side workspace criteria, ownership stamping, ID-tampering rejection; All Companies limited to workspace |
| Company profile and preferences | Working rich architecture | Preserved description, services, capabilities, experience, sectors, countries and exclusions; scoped creation/editing |
| Discover | Working stored qualifying-match feed | Preserved gates/freshness; added submission actions and useful empty state; no AI on GET |
| Source management | Existing catalog enable/disable | Admin-only add/enable/disable; public catalog clearly distinguished from private workspace data |
| Scan initiation | Unbounded shared scan action | Company-selected, source-selected scan limited to three pages; general scan is operator-only |
| Opportunity submission | Missing | URL or pasted notice, shared analyzer/cache/verification/matching/triage, private receipts |
| Opportunity Detail | Working stored decision workspace | Preserved strengths/gaps/eligibility/provenance; explicit refresh and readiness actions, visible company-selection button |
| Matching | Working | Existing engine and thresholds unchanged; scoped company selection and private submission/cache boundaries |
| Pipeline | Working but global private state | Workspace-specific saved status, stage, next action and notes; shared legacy notes hidden |
| Explicit readiness | Working | Preserved Phase 8 engine; Analyze Bid Requirements button, evidence and UNKNOWN semantics |
| Settings | Missing user controls | Account/workspace identity, password change, sign-out and allowance explanation |
| Error handling | Partial | Readable fetch/AI retry paths, review/error receipts, safe form/storage errors, no credential echo |
| Production preparation | Existing configuration incomplete | Proposed Render + Neon configuration, migrations, TLS/session safeguards, health checks and operator runbook |

The AI model, prompts, matching threshold, triage/evidence/eligibility/geography policy
and fingerprint definitions were not changed. The centralized Gemini client has a
small pre-attempt budget hook; it accounts for retries without replacing retry logic.
Private submission source keys distinguish workspace copies while retaining existing
engine fingerprint/caching behavior. The local controlled-runner rendering harness
calls the existing view functions directly; actual HTTP security is tested separately.

## Security boundaries

- Every private HTTP route requires an active, unexpired database session.
- One user belongs to one workspace; workspace members share pursuit state.
- Company/preference/match/triage/readiness/checklist reads are scoped server-side.
- Private manual notices and analysis records are workspace-owned. Background shared
  discovery cannot deduplicate against private notices.
- Unowned legacy companies and legacy global pursuit notes are not exposed to beta users.
- Shared public notices remain readable, with independent private pursuit state.
- Admin actions require an administrator; admin status does not bypass company scoping.
- All mutation forms require CSRF tokens and reject conflicting Origin headers.
- Passwords use salted scrypt hashes; session/invite tokens are random and stored as
  SHA-256 digests. Sessions last 12 hours; invitations are single-use and expire in 48 hours.
- User analysis operations: 10/user/hour, 9 actual provider attempts/action, and
  90/workspace/day, persisted atomically across workers. Failures consume allowance.
- URL fetching reuses bounded crawler safeguards and pins validated public DNS results;
  TLS hostname verification, robots, no redirects, response-size and time limits remain.

These are application-level boundaries, not PostgreSQL row-level security. Operator
CLI/database access remains trusted. Self-service workspace switching is not exposed.

## Final check results

| Check | Result |
| --- | --- |
| Python 3.13.5 full suite | 822 passed, 1 opt-in PostgreSQL test skipped, 3 deprecation warnings |
| Python 3.12.7 full suite | 822 passed, 1 opt-in PostgreSQL test skipped, 3 deprecation warnings |
| Separate fresh PostgreSQL 16.2 check | Passed: full migrations, repeat upgrade, metadata parity and two-workspace journey |
| `ruff check .` on both environments | Passed |
| `mypy app` on both environments | Passed; 79 source files |
| Plain `git diff --check` | Failed on pre-existing dirty-tree whitespace; see classification below |
| Targeted sprint tracked-file whitespace check | Passed |
| Local real-HTTP smoke | Passed; no AI calls |
| Visual browser screenshots | Unavailable: no connected browser surface |

Python 3.13 used the declared dependency ranges with cryptography 50.0.2. A separate
Python 3.12 environment was created under `/private/tmp/radar-beta-py312` using
`python3.12 -m venv` and `pip install -r requirements-dev.txt 'cryptography<50'`
(cryptography 49.0.0), then its own Python/ruff/mypy executables ran the same checks.
Both environments resolved the existing application dependency ranges; no runtime
requirements were changed by this sprint. Repeat the checks with the exact hosted
patch version and resolved dependencies as part of staging release acceptance.

## Acceptance evidence

The mocked-provider journey covers sign-in, company creation and country preferences,
notice verification, company matching, stored Discover/Detail, saving and updating
Pipeline, explicit requirements/readiness analysis, and evidence-backed unknown
availability. It repeats the journey for a second workspace and checks ID-tampering,
private receipt visibility and independent company records. Separate tests cover:

- Independent Pipeline notes/stages for the same shared opportunity.
- Anonymous requests, CSRF/Origin rejection, single-use invitations and admin controls.
- Review and provider-error receipts, retry messaging and default-feed exclusion.
- Fetch failures, persistent operation quotas and actual client attempt budgets.
- DNS rebinding rejection, pinned HTTPS identity, three-page/same-domain source bounds.
- No unintended AI calls during repeated page rendering.

Fresh PostgreSQL **16.2**, isolated on `127.0.0.1:55439`: the complete migration
chain, repeated upgrade, ORM schema parity and the same two-workspace journey passed.
Only synthetic test data was used. The opt-in PostgreSQL test requires an EMPTY
loopback database with a `radar_beta_test_` name and a non-default port. It is skipped
in the ordinary SQLite suite and was run separately with an explicit target.

A live local Uvicorn HTTP smoke check also passed invitation activation, company
creation, sign-in, health/readiness and seven private pages. The checks included POST
form tokens, CSP headers, viewport metadata and absence of inline event handlers.
No AI operation was invoked. The app server was stopped afterward.

Visual screenshots could not be inspected: the available computer-use tool reported
no connected browsers or native app surfaces. Desktop/mobile visual acceptance remains
an external release gate. Existing responsive styles were preserved.

## Commands and checks

Inspection used `git status --short --branch`, `rg`/`rg --files`, and targeted
`cat`/`sed` reads of repository instructions, routes, models, templates, engines,
network safeguards, configuration and tests. Only the sprint-related files below
were edited; unrelated dirty-tree whitespace was deliberately preserved.

Environment and primary checks:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/mypy app
git diff --check
git -c core.whitespace=cr-at-eol diff --check
```

The repository's existing `venv` contains Windows executables, so a separate ignored
`.venv` was created for this Mac. Missing dependencies and sandbox-denied package
network/loopback/shared-memory access were resolved through approved tool escalations.
Ruff formatting was applied only to newly authored Python files. Relevant route,
security and migration subsets were also run while fixing failures.

Local PostgreSQL setup used a PyPI `pgserver==0.1.4` binary wheel unpacked under
`/private/tmp`, `initdb` under `/private/tmp/radar-beta-pgdata`, `pg_ctl` with loopback
port 55439, and `createdb` for fresh `radar_beta_test_mvp` and
`radar_beta_test_final` databases. No existing database was reused or dropped. The temporary app and PostgreSQL
servers were stopped after validation; synthetic test files remain under `/private/tmp`.

```sh
BETA_POSTGRES_TEST_URL=<explicit-empty-local-test-url> .venv/bin/python -m pytest -q tests/migrations/test_private_beta_postgresql.py
```

HTTP smoke setup used an explicitly named SQLite file under `/private/tmp`,
`python -m alembic upgrade head`, `python -m app.beta_admin invite` with a synthetic
`.test` recipient/workspace, and `python -m uvicorn app.main:app --host 127.0.0.1
--port 8766 --no-access-log`. An isolated requests script exercised only localhost.
No messages were sent to the synthetic recipient.

Production provider setup, hosted HTTPS smoke testing, backups and restore were
not performed. Exact proposed release commands/configuration and the checklist are
in [private-beta-release.md](private-beta-release.md).

## Files changed by this sprint

Existing files extended (some already modified/untracked before the sprint):

- `.env.example`, `render.yaml`
- `app/main.py`, `app/models.py`, `app/schema_status.py`
- `app/intelligence/gemini_client.py`, `app/controlled_runner.py`
- `app/templates/business_profile.html`, `detail.html`, `index.html`, `pipeline.html`, `sources.html`
- `tests/conftest.py`, `tests/integration/test_model_relationships.py`
- `tests/migrations/test_migrations.py`, `tests/routes/test_startup.py`, `tests/unit/test_schema_status.py`

New files:

- `app/beta_admin.py`, `beta_models.py`, `beta_routes.py`, `beta_scope.py`, `beta_security.py`, `beta_submission.py`
- `app/static/beta.js`
- `app/templates/access.html`, `analyze.html`, `assessments.html`, `beta_base.html`, `settings.html`
- `migrations/versions/20261030_12_private_beta.py`
- `tests/routes/test_private_beta.py`, `tests/unit/test_beta_security.py`, `tests/migrations/test_private_beta_postgresql.py`
- `docs/private-beta-release.md`, `docs/private-beta-validation.md`

## Remaining release classification

**BLOCKER — external release gates:** verify the proposed hosted runtime, HTTPS
cookies/origin, real provider entitlement/quota, configured operator alerts, a backup
schedule and successful isolated restore. Complete desktop/mobile browser acceptance.
Obtain the user's final release approval before committing, pushing or deploying.

**NON-BLOCKER:** the supplied dirty tree has pre-existing whitespace findings in
32 unrelated files, predominantly CRLF endings, plus two space-only blank lines in
`app/source_discovery.py`. Plain `git diff --check` therefore fails. Treating CRLF as
line endings still flags those two pre-existing lines. The sprint's changed tracked
files pass the targeted whitespace check. Existing deprecation warnings concern
Starlette's httpx TestClient integration and legacy `datetime.utcnow` calls; they do
not fail the tests. No unrelated AI-core files were rewritten to silence them.

**POST-MVP:** self-service membership, MFA/SSO, asynchronous job orchestration,
large-scale source monitoring, richer operator telemetry, and a document library.
No unresolved application security failure was observed in the offline checks;
this does not substitute for hosted acceptance or a separate penetration review.
