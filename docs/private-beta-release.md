# Private beta release runbook

This is a proposed release procedure. No production access, deployment, commit,
push, confidential data transfer, or real Gemini calls were performed for this sprint.
Final release approval is required before any of those actions.

## Runtime configuration

Use the existing FastAPI application on Render and a **new, empty Neon database**.
`render.yaml` describes the proposed service; applying it is a separate release action.
Automatic deployment is disabled. The paid instance supports a pre-deploy migration.
Confirm the chosen service supports that command before release; see
[Render deploy steps](https://render.com/docs/deploys) and
[Blueprint reference](https://render.com/docs/blueprint-spec).

Set these through the hosting secret configuration, never in source control:

- `APP_ENV=production`
- `PUBLIC_ORIGIN=https://<beta-host>` (no trailing slash or path)
- `DATABASE_URL`: Neon **direct**, non-pooler connection URL with TLS required.
  Both `postgresql://` and `postgresql+psycopg://` are supported. Percent-encode
  reserved characters in the password. Use the direct URL for migrations too.
- `ENABLE_INTERNAL_SCHEDULER=false`
- `GEMINI_API_KEY`: the authorized provider key
- `GEMINI_MODEL=gemini-3.5-flash-lite`: existing model, unchanged by this sprint

No session signing key is needed: cookies contain random opaque tokens; the database
stores their SHA-256 digests. Sessions expire after 12 hours and are revocable.
Production requires HTTPS cookies, HTTPS public origin, PostgreSQL, and a disabled
internal scheduler. Use the Render HTTPS endpoint/custom domain. Never expose a
parallel plaintext endpoint. `PUBLIC_ORIGIN` must match browser Origin exactly.

Build: `pip install -r requirements.txt`

Pre-deploy (operator-approved target only): `python -m alembic upgrade head`

Start: `uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --no-access-log`

Health check: `/readyz` (checks connectivity and exact Alembic head); `/healthz`
is process liveness. Neither invokes AI. There is no runtime schema creation.
See [Render health checks](https://render.com/docs/health-checks).

The current migration is forward-only. Test the full chain on a separate staging
PostgreSQL instance, then run migrations against the approved empty production DB.
Never stamp an unknown database as current. Stop on schema drift.

## Operator invitations

With an **explicit, verified target** `DATABASE_URL` already set in the operator
shell, use the same installed application version. These commands write identity
records, so run only after approval for that target:

```sh
python -m app.beta_admin invite --email '<owner-email>' --workspace-name '<workspace-name>'
python -m app.beta_admin invite --email '<colleague-email>' --workspace-id <workspace-id>
python -m app.beta_admin invite --email '<admin-email>' --workspace-name '<admin-workspace>' --admin
python -m app.beta_admin revoke --email '<email>'
python -m app.beta_admin reinvite --email '<email>'
```

Share the one-use code privately with the intended recipient. They visit
`https://<beta-host>/accept-invite` and choose a password of at least 12 characters.
Invitations expire after 48 hours. No email sending service is required. `reinvite`
revokes the old password and all sessions and issues a replacement code in the
same workspace; this is the beta's operator-assisted password recovery.
`revoke` immediately disables access and clears sessions. Do not log invitation
codes or paste them into tickets. There is no public signup or browser user admin.

Each user belongs to one workspace. Workspace members share profiles and Pipeline
state. Administrator status allows shared source enable/disable actions, but does
not bypass workspace visibility. “All companies” means all companies in the signed-in
workspace. Legacy unassigned companies stay invisible; there is deliberately no
automatic ownership assignment. Do not import or reassign development data without
explicit authorization and an ownership review.

## User workflow and operation bounds

1. Activate an invitation or sign in.
2. Create a company under Companies. Save services, capabilities, experience,
   sectors, preferred countries and exclusions using the existing rich profile.
3. Select **Analyze an opportunity** on Discover. Choose a company and paste an
   opportunity URL or up to 50,000 characters of notice text. Text avoids fetching.
4. Verified notices are analyzed and matched with the existing engines. Current,
   qualifying company matches appear in Discover. Submitted assessments retains
   rejected, review, failed and nonqualifying results, with links to verified notices.
5. Open Detail, review strengths, gaps, eligibility, source and freshness. Refresh
   company assessment explicitly after changing a company profile.
6. Add to Pipeline; edit stage, next action and notes. They belong to the workspace.
7. Select **Analyze Bid Requirements** on Detail. Unknown document evidence stays
   unknown. No readiness/analysis generation happens on GET.

Limits: 10 analysis operations/user/hour; 9 provider attempts/action including
retries; 90 provider attempts/workspace/day. Failed operations count. Counters are
atomic database records shared across web workers. Page reads never reserve AI.
Sign-in limits are per address and per normalized email. HTTPS sessions, CSRF and
Origin checks protect all mutation forms.

The Sources page lists shared discovery sources and offers **Scan selected source**
for one chosen company, bounded to three pages and the same AI attempt allowance. Administrators add public source URLs and manage their global
availability. Source names and URLs are shared catalog data; do not enter confidential
workspace information there. Adding a source performs no fetch or AI generation. User URL analysis performs a one-page bounded scan; selected-source scans reuse
the existing crawler link selection with a three-page cap. Both respect robots,
reject redirects and private IPs, pin public DNS addresses, and cap content/time.
Neither bypasses subscriptions, access controls, or unsupported document formats.
Paste the notice text when a site is inaccessible. General discovery and bulk AI
review remain operator jobs; they are unavailable through beta HTTP actions. An
operator must separately authorize and budget any background discovery run. Never
run it merely to populate a demo.

## Logging, support and recovery

Run Uvicorn without access logs: query-string search terms and URLs can be private.
Application events log operation/category and workspace ID without notice content,
passwords, tokens, provider exception bodies or database URLs. Hosting captures
stdout/stderr. Alert on `/readyz` failure, sustained 5xx, and provider authentication
or quota failures. Assign an operator to check these before inviting users.
No external error-reporting vendor is required for beta; configure hosting alerts
and restrict log access. Do not enable debug mode or raw request-body logging.

Use Neon's restore history for the purchased plan and an encrypted, access-restricted
logical backup on an agreed schedule (at least daily for active beta use). Confirm
retention in the provider dashboard before inviting users. Use a direct connection
for backups and migrations; see [Neon PostgreSQL export guidance](https://github.com/neondatabase/website/blob/main/content/docs/import/migrate-from-postgres.md).

After explicit approval and with native libpq URLs in the named shell variables:

```sh
pg_dump --dbname="$BETA_DIRECT_DATABASE_URL" --format=custom --file="$ENCRYPTED_BACKUP_STAGING_PATH"
pg_restore --dbname="$EMPTY_RECOVERY_DATABASE_URL" --no-owner --no-acl "$ENCRYPTED_BACKUP_STAGING_PATH"
```

The output must be inside approved encrypted storage; those variables are examples,
not configured secrets. Test restore into an **empty isolated recovery database**,
check Alembic head and the sign-in/company/Pipeline journey, and record recovery time.
Never run restore against a live DB. Recovery uses a verified backup plus a compatible
application release; do not downgrade ownership migrations or delete production data.

## Release checklist

- Review the complete dirty-tree diff and the sprint validation report.
- Reproduce checks on the deployment Python version using an isolated environment.
- Run a fresh PostgreSQL migration and two-workspace acceptance test on staging.
- Confirm HTTPS host, secret configuration, health check, and migration command.
- Confirm Gemini key entitlement/quota without running general discovery.
- Confirm backups, a successful isolated restore, operator alerts and support contact.
- Obtain final release approval; only then commit/push and deploy the approved revision.
- Issue two staging invitations, exercise the complete journey, then invite the first
  beta workspace. Do not reuse confidential local records as seed data.

## Scope and limitations

No billing, document library, design system, public signup or AI refinement was added.
Passwords recover through an operator invitation. Source catalog management is
administrator-only. Workspace membership is managed from the operator CLI.
Large/background job queues, self-service team administration, federated identity,
MFA and elaborate monitoring dashboards are post-MVP work. A production/staging
HTTPS smoke test, real provider entitlement, backups and restore verification remain
external release gates and must not be represented as completed offline.
