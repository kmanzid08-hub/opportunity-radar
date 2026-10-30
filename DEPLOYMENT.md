# Opportunity Radar — isolated staging acceptance

**Prepared, not authorized to deploy. No application features changed.**
Use this procedure instead of the former main-branch/Render-database/Vercel instructions.
Do not apply the repository-root `render.yaml` for staging: it names the existing service.
The isolated staging definition is [staging/render.yaml](staging/render.yaml).

## Release identity and decision

- Repository: `https://github.com/kmanzid08-hub/opportunity-radar.git`
- Current development branch: `global-saas-development`; do not switch branches.
- Current local HEAD: `ea55b553e6320a21b73c483dc1c33f9ea284b4fa`.
- HEAD message: `Checkpoint opportunity radar before Gemini migration`.
- The working tree contains the completed MVP, including untracked auth, intelligence,
  templates, tests and migrations. **HEAD is not the MVP release artifact.**
- The prepared source archive and SHA-256 manifest are under
  `/private/tmp/opportunity-radar-staging-release/`; the acceptance report records their identity.
- A source snapshot is reviewable but is not a published Git release. Git-based Render
  deployment must use a newly approved commit containing that exact candidate.

**GO for staging preparation; NO-GO for executing deployment now.** Publication of
an approved release revision, isolated resource/secret configuration, and explicit
staging deployment authorization are still required. No new feature work is needed.

## Exact proposed targets

These are new resource specifications, not claims that hosted resources exist.
The user selected new isolated targets. Provider-assigned IDs and URL remain pending.

| Setting | Staging value |
| --- | --- |
| Render service | `opportunity-radar-staging`, new Web Service |
| Render region / compute | `frankfurt` / `0.5c-512mb`, one instance; operator approves cost before provisioning |
| Repository / branch | Repository above / `global-saas-development` |
| Blueprint path | `staging/render.yaml`, never root `render.yaml` |
| Automatic deploys | `autoDeployTrigger: 'off'`; Blueprint Auto Sync also disabled |
| Python | `3.12.12` (confirm actual Linux build version in logs) |
| Build | `pip install -r requirements.txt -c staging/constraints-py312.txt && pip check` |
| Pre-deploy | `python -m alembic upgrade head` |
| Start | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-access-log` |
| Health | `/readyz`; process liveness `/healthz` |
| Neon project | New `opportunity-radar-staging`, not a clone of production |
| Neon region / PostgreSQL | `aws-eu-central-1` / major 16, provider-maintained patch |
| Neon branch | New empty root branch named `staging` (rename only the new project's default branch if needed) |
| Neon database / owner role | `radar_staging` / `radar_staging_owner` |
| Recovery target | Separate new project `opportunity-radar-staging-restore`, empty database `radar_staging` |
| Production data / scheduled discovery | None / disabled |

Render settings follow the [Blueprint reference](https://render.com/docs/blueprint-spec).
Frankfurt is supported by [Neon](https://neon.com/docs/introduction/regions).
No additional authentication service or public signup is required.

## Secrets and security verification

Set secrets only in the new staging service. Never send them in chat, commit them,
copy a local `.env`, or reuse production credentials. No GitHub secret change is needed.

| Variable | Required value / operator action |
| --- | --- |
| `APP_ENV` | `production`, including staging, to exercise secure runtime checks |
| `ENABLE_INTERNAL_SCHEDULER` | `false` |
| `PUBLIC_ORIGIN` | Manifest self-reference to `RENDER_EXTERNAL_URL`; verify the assigned `https://...onrender.com` origin has no trailing slash/path |
| `DATABASE_URL` | Staging Neon **direct**, non-pooler URL for database `radar_staging`, with `sslmode=require` or stronger; retain provider-required `channel_binding` parameters |
| `GEMINI_API_KEY` | Dedicated staging key/project, model access and quota confirmed by operator |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite`, existing model unchanged |

The PUBLIC_ORIGIN self-reference follows Render's
[documented generated-URL pattern](https://render.com/tutorials/secure-mcp-server-on-render/blueprint-and-deploy).
Use only the selected origin for acceptance; an additional hostname requires an
explicit origin configuration review, not a relaxation of CSRF.

Code review and existing tests verify invite-only access, salted scrypt passwords,
12-hour opaque revocable sessions, single-use 48-hour invitations, CSRF/Origin checks,
Secure/HttpOnly/SameSite cookies under production settings, and workspace-scoped
profiles, assessments and Pipeline. Admin status does not bypass tenant isolation.
These are application-level checks, not PostgreSQL row-level security.

Production startup rejects missing HTTPS origin, SQLite, missing PostgreSQL TLS,
missing Gemini key and enabled internal scheduler. `/readyz` requires database access
and exact migration revision `20261030_12`. GET renders do not invoke Gemini.
No runtime schema creation or automatic production data assignment is permitted.

Staging must verify the actual Linux dependency installation and provider permissions.
The constraints file captures the tested Python 3.12 dependency versions; it does not
claim wheel/hash parity across macOS and Linux. No provider key was exercised here.

## Execution procedure — only after explicit approval

1. **Review and approve the candidate.** Verify the archive checksum and file manifest.
   Review the complete intended MVP diff, including untracked files and deleted legacy
   migration scripts. Do not commit `.env`, databases, logs, caches or local backups.
2. **Approve commit and push separately from deployment.** Confirm that pushing this
   development branch cannot auto-deploy an existing production service or auto-sync
   an existing Blueprint. Do not modify production settings to make this happen without
   authorization. Stage only the reviewed release paths, then run:

   ```sh
   git diff --cached --stat
   git diff --cached --name-status
   git commit -m "Prepare invite-only beta staging release"
   git rev-parse HEAD
   git push origin HEAD:global-saas-development
   git ls-remote origin refs/heads/global-saas-development
   ```

   Record the resulting full SHA as `RELEASE_COMMIT`. Verify the exported commit's
   application/build file hashes match the prepared candidate. Do not deploy `ea55b55`.
   Do not add, switch to, merge into or push `main`.
3. **Provision the new empty Neon project after authorization.** Select the exact
   region/version/branch/database/role above. Do not branch from a production project.
   Record project ID, branch ID and endpoint hostname without recording the password.
   Obtain the direct TLS connection string privately. Verify database `radar_staging`
   and no application tables before the first migration. Do not run a seed command.
4. **Authorize the first staging deployment.** Connect the private repository to a
   new Render Blueprint using branch `global-saas-development` and path
   `staging/render.yaml`. Confirm no existing service/resource is in its plan.
   Set the two private inputs (`DATABASE_URL`, `GEMINI_API_KEY`). Review the generated
   PUBLIC_ORIGIN self-reference. **Creating/applying the Blueprint triggers its initial
   deploy even with auto-deploy off. Do not click Apply before authorization.**
   Confirm the branch tip is still exactly `RELEASE_COMMIT` before applying. Disable
   Blueprint Auto Sync and keep service auto-deploy off. Later deployments use
   Manual Deploy > Deploy a specific commit > the approved full SHA.
5. **Verify deployment identity and migrations.** Record Render service/deploy IDs,
   deployed commit SHA, Python version and installed dependency versions. Build must
   pass `pip check`; pre-deploy must finish at `20261030_12`. On any migration failure,
   stop and inspect; never stamp, reset, downgrade or restore over that database.
6. **Check the assigned HTTPS service** with its actual URL in `STAGING_ORIGIN`:

   ```sh
   curl --fail --silent --show-error "$STAGING_ORIGIN/healthz"
   curl --fail --silent --show-error "$STAGING_ORIGIN/readyz"
   curl --silent --show-error --dump-header - --output /dev/null "$STAGING_ORIGIN/pipeline"
   ```

   Expect `{"status":"ok"}`, `{"status":"ready"}`, and anonymous Pipeline redirect
   to `/login`, respectively. Verify TLS, secure cookie attributes, HSTS and CSP in
   the browser. Never bypass a certificate warning.
7. **Issue two staging invitations** from the Render service shell (whose DATABASE_URL
   must be verified as the staging project), or an approved operator shell using that
   same staged release and explicitly configured staging URL:

   ```sh
   python -m app.beta_admin invite --email '<tester-A-email>' --workspace-name 'Staging A'
   python -m app.beta_admin invite --email '<tester-B-email>' --workspace-name 'Staging B'
   ```

   Share codes privately. Testers activate at `/accept-invite` and choose their own
   passwords. No email is sent by the CLI. Use synthetic company/opportunity content.
   Provision an administrator only if source catalog acceptance requires one.
8. **Run the checklist below**, one known public notice URL and one pasted synthetic
   notice. Use a small explicitly approved provider-call budget within the existing
   limits; do not run general discovery, change thresholds, or generate bulk test data.
9. **Complete isolated recovery verification**, configure operator health/error alerts,
   record all outcomes, and decide GO/NO-GO for inviting the first beta users.
   A staging approval does not authorize a production deploy or production migration.

The explicit-commit deploy option is documented by
[Render](https://render.com/docs/deploys). Blueprint automatic sync is a separate
control from service auto-deploy ([documentation](https://render.com/docs/infrastructure-as-code)).

## Short browser acceptance checklist

Use separate browser profiles for A and B; test desktop and a mobile browser/viewport.
Record PASS/FAIL, tester, timestamp, device and deployed SHA. Do not record passwords,
invitation codes or confidential notices in screenshots.

- [ ] A activates an invitation, signs in, signs out; private URLs redirect after logout.
- [ ] A creates and edits a company, services/capabilities and preferred countries; saved values persist after sign-in again.
- [ ] A submits a public opportunity URL and notice text. Blocked URL gives a helpful text fallback; analysis and company match complete without changing intelligence.
- [ ] Discover shows current qualifying matches; review/nonqualifying results remain accessible in Submitted assessments. Detail shows source, score, reasons and gaps.
- [ ] A saves to Pipeline and changes stage/next action/notes; values persist on revisit.
- [ ] A explicitly selects Analyze Bid Requirements; requirements/evidence/gaps/next actions appear. Unsupported document availability remains UNKNOWN.
- [ ] B sees only B's companies/assessments/Pipeline. A's private IDs in GET and POST URLs return 404/403; admin controls are protected. Shared public notices may be visible, private notes must not be.
- [ ] Repeated GET page loads create no provider requests. Confirm Secure/HttpOnly/SameSite cookies, matching Origin, usable forms and no unexpected console errors.
- [ ] Desktop and mobile: navigation, keyboard labels/focus, readable forms, loading/error/empty states and no obstructed controls or horizontal layout failure.

## Backup and restore — isolated verification only

The local synthetic drill passed; its evidence is in the staging acceptance report.
Before inviting real beta users, choose an owner, daily encrypted backup storage,
retention/history window and restore target. Staging drill target must be a separate
empty project/database; never a production URL or the active staging database.
Use PostgreSQL 16 client tools, direct unpooled TLS URLs and matching release code.
Follow [Neon's backup guidance](https://neon.com/docs/manage/backup-pg-dump).

With target URLs loaded privately into `STAGING_DIRECT_URL` and `RESTORE_DIRECT_URL`,
and `BACKUP_FILE` on approved encrypted storage, verify both provider project IDs,
endpoint hosts and database names; verify target has zero application tables. Then:

```sh
umask 077
pg_dump --dbname="$STAGING_DIRECT_URL" --format=custom --file="$BACKUP_FILE"
shasum -a 256 "$BACKUP_FILE"
pg_restore --list "$BACKUP_FILE"
pg_restore --dbname="$RESTORE_DIRECT_URL" --exit-on-error --single-transaction --no-owner --no-acl "$BACKUP_FILE"
```

No `--clean`, database drop, truncate, reset or overwrite is allowed. Do not rerun a
restore into a populated target. Stop on any error. Do not run Alembic against the
restore target before importing the dump, because the target must start empty.

Verify revision `20261030_12`, table counts/content, sequence state and current profile,
match, Pipeline and readiness records. Test restored sign-in and two-workspace isolation
using the same candidate in an isolated operator test process; do not point the active
staging service at the restore target. Provider generation must stay disabled during
recovery verification. Record dump checksum, data comparison, recovery duration and
operator sign-off. Retain dump only in approved encrypted storage; record metadata
without passwords/tokens. If recovery is needed, restore elsewhere and make a separately
approved traffic/configuration decision; this procedure never replaces production data.

## Genuine release gates

For staging execution: approved/published candidate SHA; new resource IDs and staging
secrets; explicit authorization to create/apply the staging deployment. Remote resources
have not been provisioned or inspected, so their identity/security cannot be marked PASS.

For beta-user access: successful hosted build/migrations/TLS/provider journey, desktop
and mobile acceptance, cross-workspace check and hosted isolated recovery/backup ownership.
Existing whitespace/deprecation warnings are non-blockers. No redesign, AI refinement,
new features, billing or document-management work is required.
